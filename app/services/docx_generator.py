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
            
            # Parse the HTML content
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Handle document structure based on the 'document' class
            document_div = soup.find('div', class_='document')
            if document_div:
                # Process each page in the document
                for page_div in document_div.find_all('div', class_='page'):
                    # Remove any DOCTYPE, meta tags, and style tags
                    for tag in page_div.find_all(['meta', 'style', 'title']):
                        tag.decompose()
                    
                    # Process the content of the page
                    self._process_page_content(doc, page_div)
                    
                    # Add page break between pages if this isn't the last page
                    if page_div != document_div.find_all('div', class_='page')[-1]:
                        doc.add_page_break()
            else:
                # Fall back to processing the entire content if no document structure
                self._process_page_content(doc, soup)
            
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

    def _process_page_content(self, doc, page_content):
        """Process the content of a page."""
        # Extract the content, skipping doctype and other irrelevant tags
        content_elements = []
        for element in page_content.children:
            # Skip NavigableString, doctype, and other irrelevant elements
            if isinstance(element, NavigableString) or element.name in ['!doctype', 'meta', 'style', 'title']:
                continue
            if element.name:
                content_elements.append(element)
        
        # If no elements found, try to look deeper
        if not content_elements:
            content_elements = page_content.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'table', 'section', 'article'])
        
        # Process each content element in order
        for element in content_elements:
            self._process_element(doc, element)

    def _process_element(self, doc, element):
        """Process an HTML element based on its type."""
        if element.name in ['article', 'section', 'div']:
            # For container elements, process their children
            for child in element.children:
                if isinstance(child, NavigableString):
                    if child.strip():
                        doc.add_paragraph(child.strip())
                elif child.name:
                    self._process_element(doc, child)
                    
        elif element.name in ['h1', 'h2', 'h3', 'h4']:
            # Process headings
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
                        para = doc.add_paragraph(child.strip())
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
                        para = doc.add_paragraph(child.strip())
                        para.style = 'Header'
                elif child.name:
                    if child.name == 'p':
                        para = doc.add_paragraph()
                        self._process_text_content(para, child)

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
            
            # Process each cell
            for j, cell in enumerate(cells):
                if j < max_cols:
                    try:
                        table_cell = table.cell(i, j)
                        
                        # Handle cell content
                        para = table_cell.paragraphs[0]
                        
                        # Process text content
                        self._process_text_content(para, cell)
                        
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
                            for run in para.runs:
                                run.bold = True
                            self._set_cell_shading(table_cell, 'f2f2f2')  # Light gray for headers

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