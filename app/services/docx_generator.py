import io
import base64
import re
from bs4 import BeautifulSoup, NavigableString
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import logging

logger = logging.getLogger(__name__)

class DocxGeneratorService:
    def generate_docx(self, html_content: str) -> bytes:
        """Convert HTML to DOCX with improved structure preservation."""
        try:
            doc = Document()
            
            # Configure default paragraph and document settings
            style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            
            # Set proper document margins
            sections = doc.sections
            for section in sections:
                section.left_margin = Inches(1)
                section.right_margin = Inches(1)
                section.top_margin = Inches(1)
                section.bottom_margin = Inches(1)
            
            # Clean up the HTML content before processing
            html_content = html_content.replace('&nbsp;', ' ')
            
            # Create proper heading styles for document hierarchy
            self._ensure_heading_styles(doc)
            
            # Parse the HTML content
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Debug logging
            logger.info(f"Starting document conversion, HTML length: {len(html_content)}")
            
            # Remove style and script tags
            for tag in soup.find_all(['style', 'script']):
                tag.decompose()
            
            # Process document body
            self._process_document_body(doc, soup)
            
            # Save document to bytes
            docx_stream = io.BytesIO()
            doc.save(docx_stream)
            docx_stream.seek(0)
            
            logger.info("DOCX generation completed successfully")
            return docx_stream.getvalue()
            
        except Exception as e:
            logger.error(f"Error generating DOCX: {str(e)}")
            # Create a simple error document
            error_doc = Document()
            error_doc.add_heading("Error Creating Document", 0)
            error_doc.add_paragraph(f"An error occurred while creating the document: {str(e)}")
            error_stream = io.BytesIO()
            error_doc.save(error_stream)
            error_stream.seek(0)
            return error_stream.getvalue()

    def _ensure_heading_styles(self, doc):
        """Ensure all heading styles are properly configured."""
        heading_sizes = {
            'Heading 1': 16,
            'Heading 2': 14,
            'Heading 3': 13,
            'Heading 4': 12,
        }
        
        for name, size in heading_sizes.items():
            style = doc.styles[name]
            style.font.name = 'Arial'
            style.font.size = Pt(size)
            style.font.bold = True
            # Add proper spacing
            style.paragraph_format.space_before = Pt(12)
            style.paragraph_format.space_after = Pt(6)

    def _process_document_body(self, doc, soup):
        """Process the main document body with improved structure handling."""
        # Find the main content
        main_content = soup.body or soup
        
        # Track if we're in a table section to avoid nested tables
        in_table_section = False
        
        # Process elements in order
        for element in self._get_content_elements(main_content):
            if isinstance(element, NavigableString):
                if element.strip():
                    p = doc.add_paragraph()
                    p.add_run(element.strip())
                continue
                
            # Handle by element type
            if element.name in ['h1', 'h2', 'h3', 'h4']:
                # Clear flag when hitting a heading
                in_table_section = False
                self._process_heading(doc, element)
            
            elif element.name == 'table':
                in_table_section = True
                self._process_table(doc, element)
            
            elif element.name in ['ul', 'ol']:
                in_table_section = False
                self._process_list(doc, element)
            
            elif element.name in ['p', 'div']:
                # Skip empty paragraphs
                if not element.get_text(strip=True):
                    continue
                    
                # Check if this looks like a table row but isn't in a table
                if not in_table_section and self._looks_like_table_row(element):
                    # This could be a table row represented as a paragraph
                    # Try to collect adjacent similar elements to form a table
                    table_rows = self._collect_table_rows(element)
                    if len(table_rows) > 1:
                        self._create_table_from_paragraphs(doc, table_rows)
                        continue
                        
                # Process as normal paragraph
                self._process_paragraph(doc, element)
                
            elif element.name == 'hr':
                doc.add_paragraph().add_run('_' * 50)
                
            else:
                # Default handling for other elements
                text = element.get_text(strip=True)
                if text:
                    p = doc.add_paragraph()
                    p.add_run(text)

    def _get_content_elements(self, parent):
        """Get meaningful content elements, filtering out empty nodes."""
        elements = []
        for child in parent.children:
            if isinstance(child, NavigableString):
                if child.strip():
                    elements.append(child)
            elif child.name not in ['style', 'script', 'meta']:
                elements.append(child)
        return elements

    def _process_heading(self, doc, element):
        """Process HTML headings with proper formatting."""
        level = int(element.name[1])  # h1 -> 1, h2 -> 2, etc.
        heading = doc.add_heading(level=level)
        self._process_text_content(heading, element)
        
        # For article headings in legal documents, add special formatting
        text = element.get_text(strip=True)
        if re.match(r'^(Article|Section|მუხლი)\s+\d+', text):
            heading.paragraph_format.space_after = Pt(6)
            heading.paragraph_format.keep_with_next = True

    def _process_paragraph(self, doc, element):
        """Process paragraph elements preserving formatting."""
        # Skip if empty
        if not element.get_text(strip=True):
            return
            
        # Create paragraph
        para = doc.add_paragraph()
        
        # Check for alignment
        if 'align' in element.attrs:
            if element['align'] == 'center':
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif element['align'] == 'right':
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                
        # Check for indentation in style
        style = element.get('style', '')
        if 'margin-left' in style:
            match = re.search(r'margin-left:\s*(\d+)(px|pt|em)', style)
            if match:
                value = int(match.group(1))
                unit = match.group(2)
                # Convert to inches (approximate)
                if unit == 'px':
                    para.paragraph_format.left_indent = Pt(value)
                elif unit == 'pt':
                    para.paragraph_format.left_indent = Pt(value)
                elif unit == 'em':
                    para.paragraph_format.left_indent = Pt(value * 12)  # Rough approximation
        
        # Process paragraph content
        self._process_text_content(para, element)

    def _process_text_content(self, paragraph, element):
        """Process the text content of an element including formatting."""
        if isinstance(element, NavigableString):
            if element.strip():
                paragraph.add_run(element.strip())
            return
            
        # Process children
        for child in element.children:
            if isinstance(child, NavigableString):
                text = child.strip()
                if text:
                    run = paragraph.add_run(text)
            elif child.name == 'br':
                paragraph.add_run().add_break()
            elif child.name in ['strong', 'b']:
                run = paragraph.add_run(child.get_text())
                run.bold = True
            elif child.name in ['em', 'i']:
                run = paragraph.add_run(child.get_text())
                run.italic = True
            elif child.name == 'u':
                run = paragraph.add_run(child.get_text())
                run.underline = True
            elif child.name == 'a':
                run = paragraph.add_run(child.get_text())
                run.underline = True
                run.font.color.rgb = RGBColor(0, 0, 255)
            elif child.name == 'span':
                # Process span with potential inline styles
                run = paragraph.add_run(child.get_text())
                style = child.get('style', '')
                if 'bold' in style or 'font-weight' in style:
                    run.bold = True
                if 'italic' in style or 'font-style: italic' in style:
                    run.italic = True
            else:
                # Recursively process other elements
                self._process_text_content(paragraph, child)

    def _process_table(self, doc, table_elem):
        """Process HTML tables with proper structure preservation."""
        rows = table_elem.find_all('tr')
        if not rows:
            return
            
        # Determine table dimensions
        max_cols = 0
        for row in rows:
            cells = row.find_all(['td', 'th'])
            max_cols = max(max_cols, len(cells))
            
        if max_cols == 0:
            return
            
        # Create the table
        table = doc.add_table(rows=len(rows), cols=max_cols)
        table.style = 'Table Grid'
        
        # Process header rows first
        for i, row in enumerate(rows):
            cells = row.find_all(['td', 'th'])
            
            # Process each cell
            for j, cell in enumerate(cells):
                if j < max_cols:
                    table_cell = table.cell(i, j)
                    para = table_cell.paragraphs[0]
                    
                    # Process alignment
                    if cell.get('align') == 'center':
                        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    elif cell.get('align') == 'right':
                        para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                        
                    # Check for rowspan/colspan
                    rowspan = int(cell.get('rowspan', 1))
                    colspan = int(cell.get('colspan', 1))
                    
                    if rowspan > 1 or colspan > 1:
                        # Handle spanning cells
                        self._merge_cells(table, i, j, i + rowspan - 1, j + colspan - 1)
                    
                    # Bold for header cells
                    if cell.name == 'th':
                        run = para.add_run(cell.get_text(strip=True))
                        run.bold = True
                    else:
                        self._process_text_content(para, cell)
        
        return table

    def _merge_cells(self, table, start_row, start_col, end_row, end_col):
        """Merge cells in the table - helper for spanning cells."""
        try:
            cell_1 = table.cell(start_row, start_col)
            cell_2 = table.cell(end_row, end_col)
            cell_1.merge(cell_2)
        except Exception as e:
            logger.warning(f"Failed to merge cells: {e}")

    def _process_list(self, doc, list_elem):
        """Process HTML lists maintaining proper structure."""
        is_ordered = list_elem.name == 'ol'
        items = list_elem.find_all('li', recursive=False)
        
        for i, item in enumerate(items):
            # Determine list style based on type
            style_name = 'List Number' if is_ordered else 'List Bullet'
            
            # Create paragraph with list style
            p = doc.add_paragraph(style=style_name)
            
            # Process content
            self._process_text_content(p, item)
            
            # Handle nested lists
            for nested_list in item.find_all(['ul', 'ol'], recursive=False):
                # Increase indentation for nested lists
                self._process_list(doc, nested_list)

    def _looks_like_table_row(self, element):
        """Check if an element looks like it should be part of a table row."""
        text = element.get_text(strip=True)
        
        # Check for tab-separated content that might represent columns
        if '\t' in text:
            return True
            
        # Check for elements with a clear column structure
        child_texts = [c.get_text(strip=True) for c in element.children if not isinstance(c, NavigableString)]
        if len(child_texts) >= 2:
            return True
            
        # Look for specific patterns like "Code [number]: [description]"
        if re.match(r'^\d+\.?\s+.*?:\s+.*$', text):
            return True
            
        return False

    def _collect_table_rows(self, start_element):
        """Collect consecutive elements that appear to be table rows."""
        rows = [start_element]
        sibling = start_element.find_next_sibling()
        
        while sibling and self._looks_like_table_row(sibling):
            rows.append(sibling)
            sibling = sibling.find_next_sibling()
            
        return rows

    def _create_table_from_paragraphs(self, doc, elements):
        """Create a table from a series of paragraph-like elements."""
        # Determine number of columns (analyze the text pattern)
        max_cols = 0
        for elem in elements:
            text = elem.get_text(strip=True)
            
            # Check if tab delimited
            if '\t' in text:
                cols = len(text.split('\t'))
                max_cols = max(max_cols, cols)
                continue
                
            # Check for column-like pattern (e.g., "Code: Description")
            match = re.match(r'^\s*(\d+\.?\s+)?([^:]+):\s*(.*)\s*$', text)
            if match:
                max_cols = max(max_cols, 3)  # Code, Label, Value
                continue
                
            # Check child structure
            cols = sum(1 for c in elem.children if not isinstance(c, NavigableString) and c.get_text(strip=True))
            max_cols = max(max_cols, cols if cols > 0 else 2)
        
        # Create table with appropriate dimensions
        max_cols = max(max_cols, 2)  # Ensure at least 2 columns
        table = doc.add_table(rows=len(elements), cols=max_cols)
        table.style = 'Table Grid'
        
        # Fill the table
        for i, elem in enumerate(elements):
            text = elem.get_text(strip=True)
            
            # Handle different patterns
            if '\t' in text:
                # Tab-delimited content
                cols = text.split('\t')
                for j, col in enumerate(cols):
                    if j < max_cols:
                        cell = table.cell(i, j)
                        cell.text = col.strip()
            
            elif re.match(r'^\s*(\d+\.?\s+)?([^:]+):\s*(.*)\s*$', text):
                # Pattern like: "Code: Description" or "11001: Document Analysis"
                match = re.match(r'^\s*(\d+\.?\s+)?([^:]+):\s*(.*)\s*$', text)
                parts = [match.group(1) or "", match.group(2) or "", match.group(3) or ""]
                
                for j, part in enumerate(parts):
                    if j < max_cols:
                        cell = table.cell(i, j)
                        cell.text = part.strip()
            
            else:
                # Use children structure if available
                children = [c for c in elem.children if not isinstance(c, NavigableString) and c.get_text(strip=True)]
                
                if children:
                    for j, child in enumerate(children):
                        if j < max_cols:
                            cell = table.cell(i, j)
                            para = cell.paragraphs[0]
                            self._process_text_content(para, child)
                else:
                    # Default to single cell content
                    cell = table.cell(i, 0)
                    cell.text = text
                    # Merge cells if only one column of content
                    if max_cols > 1:
                        self._merge_cells(table, i, 0, i, max_cols - 1)

    def get_base64_docx(self, docx_data: bytes) -> str:
        """Convert DOCX data to a base64 string."""
        return base64.b64encode(docx_data).decode('utf-8')

# Create a singleton instance
docx_generator_service = DocxGeneratorService()