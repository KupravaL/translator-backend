import io
import base64
import pandas as pd
from bs4 import BeautifulSoup, NavigableString
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


class DocxGeneratorService:
    def generate_docx(self, html_content: str) -> bytes:
        """Convert HTML to DOCX with full content capture and structured formatting."""
        doc = Document()
        # Configure default paragraph spacing and font
        style = doc.styles['Normal']
        style.font.name = 'Arial'
        style.font.size = Pt(11)
        
        # Clean up the HTML content before processing
        # Replace any non-breaking spaces with regular spaces
        html_content = html_content.replace('&nbsp;', ' ')
        
        # Parse the HTML content
        soup = BeautifulSoup(html_content, 'html.parser')

        # Debug: Print the structure to understand what we're working with
        print("Document structure found:", soup.prettify()[:500])
        
        # Remove any standalone "html" text nodes that aren't part of actual content
        for text_node in soup.find_all(text=True):
            if text_node.strip().lower() == "html" and not text_node.parent.name in ['style', 'script', 'pre', 'code']:
                text_node.extract()

        # Remove <style> and <script> tags but preserve their content for reference
        styles = {}
        for i, tag in enumerate(soup.find_all('style')):
            styles[f'style_{i}'] = tag.string
            tag.decompose()
            
        for tag in soup.find_all('script'):
            tag.decompose()

        def apply_text_formatting(run, element):
            """Apply text formatting from HTML elements to Word runs."""
            if element.name in ['strong', 'b'] or 'font-weight: bold' in element.get('style', ''):
                run.bold = True
            if element.name in ['em', 'i'] or 'font-style: italic' in element.get('style', ''):
                run.italic = True
            if element.name == 'u' or 'text-decoration: underline' in element.get('style', ''):
                run.underline = True
            
            # Handle font color if specified
            if element.get('style') and 'color:' in element.get('style'):
                color_str = element.get('style').split('color:')[1].split(';')[0].strip()
                # This is a simple implementation - for production you'd want more robust color parsing
                if color_str.startswith('#'):
                    color = color_str[1:]
                    run.font.color.rgb = RGBColor(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))

        def process_inline_element(paragraph, element):
            """Recursively process inline elements with improved handling of nested content."""
            if isinstance(element, NavigableString):
                text = str(element).strip()
                if text and text.lower() != "html":  # Skip standalone "html" text
                    run = paragraph.add_run(text)
                    # Apply any parent formatting
                    if element.parent and element.parent.name in ['strong', 'b', 'em', 'i', 'u', 'span']:
                        apply_text_formatting(run, element.parent)
                return
                
            if element.name == 'br':
                paragraph.add_run().add_break()
                return
                
            if element.name in ['strong', 'b', 'em', 'i', 'u', 'span', 'a']:
                # Process all children with this element's formatting
                for child in element.children:
                    if isinstance(child, NavigableString):
                        text = str(child).strip()
                        if text and text.lower() != "html":  # Skip standalone "html" text
                            run = paragraph.add_run(text)
                            apply_text_formatting(run, element)
                    else:
                        # For nested formatting elements, combine parent's formatting
                        process_inline_element(paragraph, child)
                return
                
            # For any other element, just process its children
            for child in element.children:
                process_inline_element(paragraph, child)

        def process_paragraph_content(paragraph, element):
            """Process block-level elements with better text flow preservation."""
            # Check if element is empty or whitespace only
            if not element.get_text(strip=True) or element.get_text(strip=True).lower() == "html":
                return
                
            # Process all children
            for child in element.children:
                if isinstance(child, NavigableString):
                    text = str(child).strip()
                    if text and text.lower() != "html":  # Skip standalone "html" text
                        paragraph.add_run(text)
                elif child.name == 'br':
                    paragraph.add_run().add_break()
                elif child.name in ['strong', 'b', 'em', 'i', 'u', 'span', 'a']:
                    process_inline_element(paragraph, child)
                else:
                    # For other elements, just process their content
                    process_inline_element(paragraph, child)

        def extract_style_property(element, property_name):
            """Extract a CSS property value from an element's style attribute."""
            style = element.get('style', '')
            if not style:
                return None
                
            for prop in style.split(';'):
                prop = prop.strip()
                if prop.startswith(property_name + ':'):
                    return prop.split(':', 1)[1].strip()
            return None

        def handle_table(table_elem):
            """Improved table handling with better structure preservation."""
            # Count rows and columns
            rows = table_elem.find_all('tr')
            if not rows:
                return
                
            # Find the maximum number of cells in any row
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
            thead = table_elem.find('thead')
            header_rows = []
            if thead:
                header_rows = thead.find_all('tr')
                for i, row in enumerate(header_rows):
                    cells = row.find_all(['th', 'td'])
                    for j, cell in enumerate(cells):
                        if j < max_cols and i < len(table.rows):
                            cell_para = table.rows[i].cells[j].paragraphs[0]
                            # Make header cells bold
                            run = cell_para.add_run()
                            run.bold = True
                            process_paragraph_content(cell_para, cell)
                            
                            # Set text alignment based on style or defaults
                            if cell.get('style') and 'text-align: center' in cell.get('style'):
                                cell_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Process body rows
            tbody = table_elem.find('tbody')
            body_rows = tbody.find_all('tr') if tbody else rows
            start_row = len(header_rows) if thead else 0
            
            for i, row in enumerate(body_rows):
                row_idx = i + start_row
                if row_idx >= len(table.rows):
                    continue
                    
                cells = row.find_all(['td', 'th'])
                for j, cell in enumerate(cells):
                    if j < max_cols:
                        cell_para = table.rows[row_idx].cells[j].paragraphs[0]
                        process_paragraph_content(cell_para, cell)
                        
                        # Handle cell alignment from inline style
                        align = extract_style_property(cell, 'text-align')
                        if align == 'center':
                            cell_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        elif align == 'right':
                            cell_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            
            # Adjust column widths if specified in styles
            no_borders = 'no-borders' in table_elem.get('class', [])
            if no_borders:
                # Remove borders
                table_no_borders(table)
                
            return table

        def handle_form_section(section):
            """Improved form section handling with better layout preservation."""
            # Form sections are special layouts that need custom handling
            for form_row in section.find_all(class_='form-row'):
                # Create a 2-column table for each form row
                table = doc.add_table(rows=1, cols=2)
                table.autofit = False
                
                # Set column widths based on the HTML layout
                table.columns[0].width = Inches(2.0)  # Label
                table.columns[1].width = Inches(4.0)  # Value
                
                # Don't add borders for form tables to match the HTML layout
                table.style = 'Table Grid'
                table_no_borders(table)
                
                word_row = table.rows[0]
                
                # Process label and value
                label = form_row.find(class_='label')
                value = form_row.find(class_='value')
                
                if label:
                    cell = word_row.cells[0]
                    para = cell.paragraphs[0]
                    run = para.add_run()
                    run.bold = True  # Labels are typically bold
                    process_paragraph_content(para, label)
                    
                if value:
                    cell = word_row.cells[1]
                    para = cell.paragraphs[0]
                    process_paragraph_content(para, value)

        def table_no_borders(table):
            """Remove borders from a table."""
            # This function removes all borders from a table
            tbl = table._element.xpath('//w:tbl')
            if tbl:
                tbl = tbl[-1]  # Get the most recently added table
                for cell in tbl.xpath('.//w:tc'):
                    tcPr = cell.xpath('./w:tcPr')
                    if tcPr:
                        tcPr = tcPr[0]
                        tcBorders = OxmlElement('w:tcBorders')
                        for border in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
                            element = OxmlElement(f'w:{border}')
                            element.set(qn('w:val'), 'nil')
                            tcBorders.append(element)
                        tcPr.append(tcBorders)

        def handle_list(list_elem):
            """Process HTML lists with proper indentation and bullets/numbers."""
            is_ordered = list_elem.name == 'ol'
            items = list_elem.find_all('li', recursive=False)
            
            for i, item in enumerate(items):
                # Create paragraph with appropriate list style
                p = doc.add_paragraph(style='List Bullet' if not is_ordered else 'List Number')
                
                # Process the list item content
                process_paragraph_content(p, item)
                
                # Handle nested lists
                nested_lists = item.find_all(['ul', 'ol'], recursive=False)
                for nested_list in nested_lists:
                    handle_list(nested_list)  # Recursive handling of nested lists

        # Find the main content - look for specific containers
        main_content = soup.find('div', class_='document') or soup.find('article') or soup.body or soup
        
        # Debug - print main content structure
        print("Main content found:", main_content.name, "with classes:", main_content.get('class', []))
        
        # Process all elements in the main container
        def process_container(container):
            """Process all elements in a container element."""
            if not container:
                return
                
            for element in list(container.children):
                if isinstance(element, NavigableString):
                    text = element.strip()
                    if text and text.lower() != "html":  # Skip standalone "html" text
                        doc.add_paragraph(text)
                elif element.name:
                    # Handle headings with appropriate levels
                    if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                        level = int(element.name[1])
                        heading = doc.add_heading(level=level)
                        process_paragraph_content(heading, element)
                        
                    # Handle paragraphs and text content
                    elif element.name == 'p' or (element.get('class') and 'text-content' in element.get('class')):
                        para = doc.add_paragraph()
                        process_paragraph_content(para, element)
                        
                    # Handle tables with improved formatting
                    elif element.name == 'table':
                        handle_table(element)
                        
                    # Handle lists properly
                    elif element.name in ['ul', 'ol']:
                        handle_list(element)
                        
                    # Handle divs and sections with special treatment for form sections
                    elif element.name in ['div', 'section']:
                        if element.get('class') and 'form-section' in element.get('class'):
                            handle_form_section(element)
                        else:
                            # For other divs, process their content
                            process_container(element)
                    
                    # Handle article elements similar to divs
                    elif element.name == 'article':
                        process_container(element)
                    
                    # For other elements, try to process their content
                    else:
                        para = doc.add_paragraph()
                        process_paragraph_content(para, element)
        
        # Process the main content
        process_container(main_content)
        
        # If the document has no content at this point, try to extract all text from the HTML
        if len(doc.paragraphs) <= 1 and not doc.paragraphs[0].text:
            print("Document appears empty, trying direct text extraction")
            for text_node in soup.find_all(text=True):
                text = text_node.strip()
                if (text and 
                    text.lower() != "html" and 
                    text_node.parent.name not in ['style', 'script', 'meta', 'head']):
                    doc.add_paragraph(text)
        
        # Set consistent paragraph spacing for better readability
        for paragraph in doc.paragraphs:
            paragraph.paragraph_format.space_after = Pt(8)

        # Save document
        docx_stream = io.BytesIO()
        doc.save(docx_stream)
        docx_stream.seek(0)
        return docx_stream.getvalue()

    def get_base64_docx(self, docx_data: bytes) -> str:
        """Convert DOCX data to a base64 string."""
        return base64.b64encode(docx_data).decode('utf-8')


docx_generator_service = DocxGeneratorService()