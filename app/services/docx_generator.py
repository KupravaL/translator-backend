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
            # Remove DOCTYPE declarations, comments, and XML declarations
            html_content = re.sub(r'<!DOCTYPE[^>]*>', '', html_content, flags=re.IGNORECASE)
            html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)
            html_content = re.sub(r'<\?xml[^>]*\?>', '', html_content)
            html_content = html_content.replace('&nbsp;', ' ')
            
            # NEW: Fix \n characters in the HTML content
            html_content = html_content.replace('\\n', ' ')
            
            # Parse the HTML content
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove all meta, title tags - we don't need them in the DOCX
            for tag in soup.find_all(['meta', 'title']):
                tag.decompose()
            
            # Extract style tags but don't remove them yet (we might need style info)
            styles = soup.find_all('style')
            
            # Find if there's a document div structure
            document_div = soup.find('div', class_='document')
            
            if document_div:
                # Process each page in the document
                for page_div in document_div.find_all('div', class_='page'):
                    # Now remove style tags from this page
                    for style_tag in page_div.find_all('style'):
                        style_tag.decompose()
                    
                    # Process the content of the page
                    self._process_content(doc, page_div)
                    
                    # Add page break between pages if this isn't the last page
                    if page_div != document_div.find_all('div', class_='page')[-1]:
                        doc.add_page_break()
            else:
                # Fall back to processing the entire content if no document structure
                # First remove style tags from the soup
                for style_tag in styles:
                    style_tag.decompose()
                
                self._process_content(doc, soup)
            
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

    def _process_content(self, doc, parent_element):
        """Process all relevant content within an element."""
        # First pass: find all top-level elements that should be processed
        top_elements = []
        
        for element in parent_element.children:
            # Skip NavigableString, doctype, and other irrelevant elements
            if isinstance(element, NavigableString):
                if element.strip():
                    # Only add non-empty strings
                    top_elements.append(element)
            elif element.name and element.name not in ['!doctype', 'meta', 'title', 'style', 'script']:
                top_elements.append(element)
        
        # If no elements found, try to look deeper
        if not top_elements:
            top_elements = parent_element.find_all(
                ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'table', 'section', 'article', 'div', 'ul', 'ol']
            )
        
        # Process each content element in order
        for element in top_elements:
            self._process_element(doc, element)

    def _clean_text(self, text):
        """Clean text by removing \n characters and extra whitespace."""
        if text is None:
            return ""
        # Replace literal \n with space
        text = text.replace('\\n', ' ')
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _process_element(self, doc, element):
        """Process an HTML element based on its type."""
        # Skip empty or None elements
        if element is None:
            return
            
        if isinstance(element, NavigableString):
            if element.strip():
                # NEW: Clean the text before adding
                doc.add_paragraph(self._clean_text(element))
            return
            
        # Skip processing certain elements
        if element.name in ['!doctype', 'meta', 'title', 'style', 'script']:
            return
            
        # Process element based on its type
        if element.name in ['article', 'section', 'div']:
            # Check if this is a section with a specific class
            class_value = element.get('class', [])
            if isinstance(class_value, list) and 'text-content' in class_value:
                # For text-content sections, process as a unit
                p = doc.add_paragraph()
                self._process_text_content(p, element)
            else:
                # For container elements, process their children
                for child in element.children:
                    if isinstance(child, NavigableString):
                        if child.strip():
                            # NEW: Clean the text before adding
                            doc.add_paragraph(self._clean_text(child))
                    elif child.name:
                        self._process_element(doc, child)
                    
        elif element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            # Process headings - convert h1-h6 to appropriate heading levels
            level = int(element.name[1])
            heading = doc.add_heading(level=level)
            self._process_text_content(heading, element)
            
        elif element.name == 'p':
            # Process paragraphs
            para = doc.add_paragraph()
            self._process_text_content(para, element)
            
        elif element.name == 'table':
            # Process tables
            self._process_table(doc, element)
            
        elif element.name in ['ul', 'ol']:
            # Process lists
            self._process_list(doc, element)
            
        elif element.name == 'footer':
            # Process footers
            for child in element.children:
                if isinstance(child, NavigableString):
                    if child.strip():
                        # NEW: Clean the text before adding
                        para = doc.add_paragraph(self._clean_text(child))
                        para.style = 'Normal'
                elif child.name:
                    if child.name == 'p':
                        para = doc.add_paragraph()
                        self._process_text_content(para, child)
                
        elif element.name == 'header':
            # Process headers
            for child in element.children:
                if isinstance(child, NavigableString):
                    if child.strip():
                        # NEW: Clean the text before adding
                        para = doc.add_paragraph(self._clean_text(child))
                        para.style = 'Header'
                elif child.name:
                    if child.name == 'p':
                        para = doc.add_paragraph()
                        self._process_text_content(para, child)
                        
        elif element.name == 'a':
            # For standalone links, add as paragraph
            para = doc.add_paragraph()
            self._process_text_content(para, element)
            
        elif element.name == 'br':
            # Add line break
            doc.add_paragraph()
            
        elif element.name in ['span', 'strong', 'em', 'b', 'i', 'u']:
            # For inline elements that appear at top level, wrap in paragraph
            para = doc.add_paragraph()
            self._process_text_content(para, element)

    def _process_text_content(self, paragraph, element):
        """Process the text content of an element including formatting."""
        if element is None:
            return
            
        if isinstance(element, NavigableString):
            # NEW: Clean the text before adding
            text = self._clean_text(element)
            if text:
                paragraph.add_run(text)
            return
        
        # Check if the element has a text node directly
        if element.string and element.string.strip():
            # NEW: Clean the text before adding
            run = paragraph.add_run(self._clean_text(element.string))
            self._apply_text_formatting(run, element)
            return
            
        # Process children
        for child in element.children:
            if isinstance(child, NavigableString):
                # NEW: Clean the text before adding
                text = self._clean_text(child)
                if text:
                    run = paragraph.add_run(text)
                    self._apply_text_formatting(run, element)
            elif child.name == 'br':
                paragraph.add_run().add_break()
            elif child.name in ['strong', 'b']:
                # NEW: Clean the text before adding
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.bold = True
                self._apply_text_formatting(run, child)
            elif child.name in ['em', 'i']:
                # NEW: Clean the text before adding
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.italic = True
                self._apply_text_formatting(run, child)
            elif child.name == 'u':
                # NEW: Clean the text before adding
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.underline = True
                self._apply_text_formatting(run, child)
            elif child.name == 'a':
                # NEW: Clean the text before adding
                text = self._clean_text(child.get_text())
                if text:
                    run = paragraph.add_run(text)
                    run.underline = True
                    run.font.color.rgb = RGBColor(0, 0, 255)
                    # Add hyperlink if href is present
                    href = child.get('href')
                    if href:
                        # Store href as a comment for reference (python-docx doesn't support hyperlinks directly)
                        run.add_comment(f"Link: {href}")
            elif child.name == 'span':
                # Process span with potential inline styles
                # NEW: Clean the text before adding
                run = paragraph.add_run(self._clean_text(child.get_text()))
                self._apply_text_formatting(run, child)
            else:
                # Recursively process other elements
                self._process_text_content(paragraph, child)

    def _apply_text_formatting(self, run, element):
        """Apply text formatting based on element attributes and styles."""
        # Check direct attributes
        if element.name in ['strong', 'b'] or element.get('style', '').find('font-weight:') >= 0:
            run.bold = True
        if element.name in ['em', 'i'] or element.get('style', '').find('font-style:italic') >= 0:
            run.italic = True
        if element.name == 'u' or element.get('style', '').find('text-decoration:underline') >= 0:
            run.underline = True
            
        # Check style attribute for more complex formatting
        style = element.get('style', '')
        
        # Font color
        color_match = re.search(r'color\s*:\s*#?([0-9a-fA-F]{6})', style)
        if color_match:
            hex_color = color_match.group(1)
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            run.font.color.rgb = RGBColor(r, g, b)
            
        # Font size
        size_match = re.search(r'font-size\s*:\s*(\d+)pt', style)
        if size_match:
            font_size = int(size_match.group(1))
            run.font.size = Pt(font_size)
            
        # Font family
        font_match = re.search(r'font-family\s*:\s*([^;]+)', style)
        if font_match:
            font_family = font_match.group(1).strip().split(',')[0].strip("'\"")
            run.font.name = font_family

    def _process_table(self, doc, table_elem):
        """Process HTML tables with proper structure preservation."""
        # Extract table header and body rows
        thead = table_elem.find('thead')
        tbody = table_elem.find('tbody')
        
        rows = []
        if thead:
            rows.extend(thead.find_all('tr'))
        if tbody:
            rows.extend(tbody.find_all('tr'))
        
        # If there's no explicit thead or tbody, get all rows directly
        if not rows:
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
        
        # Process each row
        for i, row in enumerate(rows):
            cells = row.find_all(['td', 'th'])
            
            # Handle case with fewer cells than max_cols
            if len(cells) < max_cols:
                # Fill remaining cells with empty content
                for j in range(len(cells), max_cols):
                    table.cell(i, j).text = ""
            
            # Process each cell
            for j, cell in enumerate(cells):
                if j < max_cols:
                    try:
                        table_cell = table.cell(i, j)
                        
                        # Clear the default paragraph
                        if table_cell.paragraphs:
                            p = table_cell.paragraphs[0]
                            # Remove any existing content
                            for run in p.runs:
                                p._element.remove(run._element)
                        else:
                            p = table_cell.add_paragraph()
                        
                        # Process text content
                        self._process_text_content(p, cell)
                        
                        # Check for colspan
                        colspan = int(cell.get('colspan', 1))
                        if colspan > 1 and j + colspan - 1 < max_cols:
                            # Merge cells horizontally
                            for col in range(1, colspan):
                                if j + col < max_cols:
                                    target_cell = table.cell(i, j + col)
                                    # Merge target cell into the current cell
                                    table_cell.merge(target_cell)
                        
                        # Apply cell formatting
                        if cell.name == 'th':
                            # Make header cells bold
                            for paragraph in table_cell.paragraphs:
                                for run in paragraph.runs:
                                    run.bold = True
                            self._set_cell_shading(table_cell, 'f2f2f2')  # Light gray for headers
                            
                        # Handle alignment
                        align = cell.get('align')
                        style = cell.get('style', '')
                        if align or 'text-align' in style:
                            for paragraph in table_cell.paragraphs:
                                if align == 'center' or 'text-align:center' in style:
                                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                                elif align == 'right' or 'text-align:right' in style:
                                    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                                elif align == 'left' or 'text-align:left' in style:
                                    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT

                    except Exception as e:
                        logger.warning(f"Error processing table cell: {str(e)}")
        
        return table

    def _set_cell_shading(self, cell, color_hex):
        """Set the background shading of a table cell."""
        cell_properties = cell._element.tcPr
        if cell_properties is None:
            cell_properties = OxmlElement('w:tcPr')
            cell._element.append(cell_properties)
        
        shading = OxmlElement('w:shd')
        shading.set(qn('w:fill'), color_hex)
        cell_properties.append(shading)

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

    def get_base64_docx(self, docx_data: bytes) -> str:
        """Convert DOCX data to a base64 string."""
        return base64.b64encode(docx_data).decode('utf-8')

# Create a singleton instance
docx_generator_service = DocxGeneratorService()