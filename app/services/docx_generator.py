import io
import base64
import re
from bs4 import BeautifulSoup, NavigableString
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Cm, Emu, Inches, Pt, Twips
import logging
import math

logger = logging.getLogger(__name__)

class DocxGeneratorService:
    def __init__(self):
        # Initialize standard and specialty fonts
        self._register_fonts()
        
        # Initialize styles
        self.styles = {}
        self.initialize_styles()
        
    def _register_fonts(self):
        """Register standard and specialty fonts for use in PDF."""
        try:
            # Register default fonts
            from docx.oxml.ns import qn
            from docx.oxml import OxmlElement
            
            # We'll use system fonts, but log that we're ready
            logger.info("Font system initialized for DOCX generation")
        except Exception as e:
            logger.warning(f"Error initializing fonts: {str(e)}")
    
    def initialize_styles(self):
        """Initialize text styles for the document."""
        # These will be applied to the Document when it's created
        pass

    def generate_docx(self, html_content: str) -> bytes:
        """Convert HTML to DOCX with comprehensive style preservation."""
        try:
            doc = Document()
            
            # Configure default paragraph and document settings
            style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            style.paragraph_format.space_after = Pt(8)
            
            # Add custom styles for advanced formatting
            self._add_custom_styles(doc)
            
            # Set document margins
            sections = doc.sections
            for section in sections:
                section.left_margin = Inches(1)
                section.right_margin = Inches(1)
                section.top_margin = Inches(1)
                section.bottom_margin = Inches(1)
            
            # Clean up the HTML content before processing
            html_content = self._clean_html_content(html_content)
            
            # Parse the HTML content
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove all meta, title tags - we don't need them in the DOCX
            for tag in soup.find_all(['meta', 'title', 'script']):
                tag.decompose()
            
            # Extract style tags but keep them for reference
            styles = soup.find_all('style')
            css_content = "\n".join([style.string for style in styles if style.string])
            
            # Process the document structure
            document_div = soup.find('div', class_='document')
            
            if document_div:
                # Process each page in the document
                for page_div in document_div.find_all('div', class_='page'):
                    # Remove style tags from this page
                    for style_tag in page_div.find_all('style'):
                        style_tag.decompose()
                    
                    # Process page content
                    self._process_content(doc, page_div, css_content)
                    
                    # Add page break between pages if this isn't the last page
                    if page_div != document_div.find_all('div', class_='page')[-1]:
                        doc.add_page_break()
            else:
                # If no document structure, process the entire content
                for style_tag in styles:
                    style_tag.decompose()
                
                self._process_content(doc, soup, css_content)
            
            # Add headers and footers if present
            self._add_headers_and_footers(doc, soup)
            
            # Format document for improved appearance
            self._format_document_structure(doc)
            
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

    def _clean_html_content(self, html_content):
        """Clean up HTML content for processing."""
        # Remove DOCTYPE declarations, comments, and XML declarations
        html_content = re.sub(r'<!DOCTYPE[^>]*>', '', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)
        html_content = re.sub(r'<\?xml[^>]*\?>', '', html_content)
        html_content = html_content.replace('&nbsp;', ' ')
        
        # Fix \n characters in the HTML content
        html_content = html_content.replace('\\n', ' ')
        
        return html_content
    
    def _add_custom_styles(self, doc):
        """Add custom styles to the document for advanced formatting."""
        # Heading styles with proper hierarchy
        for i in range(1, 7):
            style_name = f'CustomHeading{i}'
            if style_name not in doc.styles:
                style = doc.styles.add_style(style_name, 1)  # 1 = WD_STYLE_TYPE.PARAGRAPH
                style.base_style = doc.styles['Heading' + str(i)]
                style.font.name = 'Arial'
                style.font.bold = True
                style.font.size = Pt(20 - (i * 2))  # Decreasing size for each heading level
                style.paragraph_format.space_before = Pt(12)
                style.paragraph_format.space_after = Pt(6)
        
        # Custom paragraph styles
        if 'CustomNormal' not in doc.styles:
            style = doc.styles.add_style('CustomNormal', 1)
            style.base_style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            style.paragraph_format.space_after = Pt(8)
        
        # Add RTL style for right-to-left languages
        if 'RTLParagraph' not in doc.styles:
            style = doc.styles.add_style('RTLParagraph', 1)
            style.base_style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            
            # Set RTL text direction using XML directly
            style_element = style._element
            if hasattr(style_element, 'get_or_add_pPr'):
                pPr = style_element.get_or_add_pPr()
                bidi = OxmlElement('w:bidi')
                bidi.set(qn('w:val'), "1")
                pPr.append(bidi)
        
        # Add List styles
        if 'CustomBulletList' not in doc.styles:
            style = doc.styles.add_style('CustomBulletList', 1)
            style.base_style = doc.styles['List Bullet']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
        
        if 'CustomNumberList' not in doc.styles:
            style = doc.styles.add_style('CustomNumberList', 1)
            style.base_style = doc.styles['List Number']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
    
    def _format_document_structure(self, doc):
        """Post-process the document to ensure consistent formatting."""
        # Track paragraphs to modify or delete
        paragraphs_to_delete = []
        article_pattern = re.compile(r'^Article\s+(\d+)(.*)$')
        
        # First pass: identify articles and structure issues
        for i, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            
            # Skip empty paragraphs
            if not text:
                continue
            
            # Fix Article headings
            article_match = article_pattern.match(text)
            if article_match and not text.startswith('##'):
                article_num = article_match.group(1)
                article_content = article_match.group(2).strip()
                
                # Create the heading with proper formatting
                new_heading = doc.add_paragraph(f"Article {article_num}")
                new_heading.style = 'Heading 2'
                
                # If there's content after the article number, add as a new paragraph
                if article_content:
                    content_para = doc.add_paragraph(article_content)
                
                # Mark the original for deletion
                paragraphs_to_delete.append(para)
        
        # Second pass: center main headings
        for para in doc.paragraphs:
            if para.style.name in ['Heading 1', 'Title', 'CustomHeading1']:
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Apply deletions (from end to beginning to preserve indices)
        for para in reversed(paragraphs_to_delete):
            p_element = para._element
            if p_element.getparent() is not None:
                p_element.getparent().remove(p_element)
    
    def _extract_css_styles(self, css_content, element):
        """Extract CSS styles for an element based on its classes and IDs."""
        if not css_content or not element.get('class'):
            return {}
        
        # This is a simplified CSS parser - for real-world use, consider a dedicated CSS parser
        # For now, we'll just look for classes that match our element
        extracted_styles = {}
        
        # Get element classes
        element_classes = element.get('class', [])
        if isinstance(element_classes, str):
            element_classes = [element_classes]
        
        # Get element ID
        element_id = element.get('id', '')
        
        # Simple regex-based CSS parsing
        for class_name in element_classes:
            class_pattern = r'\.{}[^{{]*{{([^}}]*)'
            class_matches = re.findall(class_pattern.format(class_name), css_content)
            
            for match in class_matches:
                # Extract style properties
                properties = [prop.strip() for prop in match.split(';') if prop.strip()]
                for prop in properties:
                    if ':' in prop:
                        key, value = prop.split(':', 1)
                        extracted_styles[key.strip()] = value.strip()
        
        # Look for ID-specific styles
        if element_id:
            id_pattern = r'#{}[^{{]*{{([^}}]*)'
            id_matches = re.findall(id_pattern.format(element_id), css_content)
            
            for match in id_matches:
                # Extract style properties
                properties = [prop.strip() for prop in match.split(';') if prop.strip()]
                for prop in properties:
                    if ':' in prop:
                        key, value = prop.split(':', 1)
                        extracted_styles[key.strip()] = value.strip()
        
        return extracted_styles

    def _add_headers_and_footers(self, doc, soup):
        """Add headers and footers to the document if present in HTML."""
        # Find header content
        header_elem = soup.find('header')
        if header_elem:
            for section in doc.sections:
                header = section.header
                header_para = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
                self._process_text_content(header_para, header_elem)
                header_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                
        # Find footer content
        footer_elem = soup.find('footer')
        if footer_elem:
            for section in doc.sections:
                footer = section.footer
                footer_para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
                self._process_text_content(footer_para, footer_elem)
                footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    def _process_content(self, doc, parent_element, css_content=None):
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
            self._process_element(doc, element, css_content)

    def _clean_text(self, text):
        """Clean text by removing \n characters and extra whitespace."""
        if text is None:
            return ""
        # Replace literal \n with space
        text = text.replace('\\n', ' ')
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _process_element(self, doc, element, css_content=None):
        """Process an HTML element based on its type."""
        # Skip empty or None elements
        if element is None:
            return
            
        if isinstance(element, NavigableString):
            if element.strip():
                # Clean the text before adding
                doc.add_paragraph(self._clean_text(element))
            return
            
        # Skip processing certain elements
        if element.name in ['!doctype', 'meta', 'title', 'style', 'script']:
            return
            
        # Get any inline and CSS styles
        inline_style = element.get('style', '')
        css_styles = self._extract_css_styles(css_content, element) if css_content else {}
        
        # Check for RTL text direction
        is_rtl = False
        if 'direction:rtl' in inline_style or 'direction: rtl' in inline_style:
            is_rtl = True
        elif css_styles.get('direction') == 'rtl':
            is_rtl = True
        
        # Process element based on its type
        if element.name in ['article', 'section', 'div']:
            # Check if this is a section with a specific class
            class_value = element.get('class', [])
            if isinstance(class_value, list) and 'text-content' in class_value:
                # For text-content sections, process as a unit
                p = doc.add_paragraph()
                self._process_text_content(p, element, is_rtl=is_rtl)
            else:
                # For container elements, process their children
                for child in element.children:
                    if isinstance(child, NavigableString):
                        if child.strip():
                            # Clean the text before adding
                            para = doc.add_paragraph(self._clean_text(child))
                            if is_rtl:
                                para.style = 'RTLParagraph'
                    elif child.name:
                        self._process_element(doc, child, css_content)
                    
        elif element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            # Process headings - convert h1-h6 to appropriate heading levels
            level = int(element.name[1])
            heading = doc.add_heading(level=level)
            self._process_text_content(heading, element, is_rtl=is_rtl)
            
            # Apply custom styles for better formatting
            style_name = f'CustomHeading{level}'
            if style_name in doc.styles:
                heading.style = style_name
            
        elif element.name == 'p':
            # Process paragraphs
            para = doc.add_paragraph()
            
            # Apply RTL style if needed
            if is_rtl:
                para.style = 'RTLParagraph'
                
            # Process text content with formatting
            self._process_text_content(para, element, is_rtl=is_rtl)
            
            # Apply alignment
            if 'text-align:center' in inline_style or 'text-align: center' in inline_style:
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif 'text-align:right' in inline_style or 'text-align: right' in inline_style:
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            elif 'text-align:justify' in inline_style or 'text-align: justify' in inline_style:
                para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            elif css_styles.get('text-align') == 'center':
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif css_styles.get('text-align') == 'right':
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            elif css_styles.get('text-align') == 'justify':
                para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            
        elif element.name == 'table':
            # Process tables
            self._process_table(doc, element, is_rtl=is_rtl)
            
        elif element.name in ['ul', 'ol']:
            # Process lists
            self._process_list(doc, element, is_rtl=is_rtl)
            
        elif element.name == 'footer':
            # Process footers - we'll handle these separately in headers and footers
            pass
                
        elif element.name == 'header':
            # Process headers - we'll handle these separately in headers and footers
            pass
                
        elif element.name == 'a':
            # For standalone links, add as paragraph
            para = doc.add_paragraph()
            if is_rtl:
                para.style = 'RTLParagraph'
            self._process_text_content(para, element, is_rtl=is_rtl)
            
        elif element.name == 'br':
            # Add line break
            doc.add_paragraph()
            
        elif element.name in ['span', 'strong', 'em', 'b', 'i', 'u', 's', 'strike', 'sub', 'sup']:
            # For inline elements that appear at top level, wrap in paragraph
            para = doc.add_paragraph()
            if is_rtl:
                para.style = 'RTLParagraph'
            self._process_text_content(para, element, is_rtl=is_rtl)
            
        elif element.name == 'pre' or element.name == 'code':
            # For code blocks, preserve formatting
            code_para = doc.add_paragraph()
            code_run = code_para.add_run(self._clean_text(element.get_text()))
            code_run.font.name = 'Courier New'
            code_run.font.size = Pt(9)

    def _process_text_content(self, paragraph, element, is_rtl=False):
        """Process the text content of an element including formatting."""
        if element is None:
            return
            
        if isinstance(element, NavigableString):
            # Clean the text before adding
            text = self._clean_text(element)
            if text:
                run = paragraph.add_run(text)
                if is_rtl:
                    self._set_run_rtl(run)
            return
        
        # Check if the element has a text node directly
        if element.string and element.string.strip():
            # Clean the text before adding
            run = paragraph.add_run(self._clean_text(element.string))
            if is_rtl:
                self._set_run_rtl(run)
            self._apply_text_formatting(run, element)
            return
            
        # Process children
        for child in element.children:
            if isinstance(child, NavigableString):
                # Clean the text before adding
                text = self._clean_text(child)
                if text:
                    run = paragraph.add_run(text)
                    if is_rtl:
                        self._set_run_rtl(run)
                    self._apply_text_formatting(run, element)
            elif child.name == 'br':
                paragraph.add_run().add_break()
            elif child.name in ['strong', 'b']:
                # Clean the text before adding
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.bold = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name in ['em', 'i']:
                # Clean the text before adding
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.italic = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name == 'u':
                # Clean the text before adding
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.underline = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name in ['s', 'strike', 'del']:
                # Handle strikethrough text
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.font.strike = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name == 'sub':
                # Handle subscript
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.font.subscript = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name == 'sup':
                # Handle superscript
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.font.superscript = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name == 'a':
                # Clean the text before adding
                text = self._clean_text(child.get_text())
                if text:
                    run = paragraph.add_run(text)
                    run.underline = True
                    run.font.color.rgb = RGBColor(0, 0, 255)
                    if is_rtl:
                        self._set_run_rtl(run)
                    
                    # Add hyperlink - we'll store href as an add-in property
                    href = child.get('href')
                    if href:
                        try:
                            # Add to hyperlinks collection via XML
                            self._add_hyperlink(paragraph, text, href)
                        except:
                            # If hyperlink creation fails, at least preserve the URL
                            pass
            elif child.name == 'span':
                # Process span with potential inline styles
                # Clean the text before adding
                run = paragraph.add_run(self._clean_text(child.get_text()))
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name == 'mark':
                # Handle highlighted text
                run = paragraph.add_run(self._clean_text(child.get_text()))
                run.font.highlight_color = 6  # WD_COLOR_INDEX.YELLOW
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            else:
                # Recursively process other elements
                self._process_text_content(paragraph, child, is_rtl=is_rtl)

    def _set_run_rtl(self, run):
        """Set a run to display right-to-left text."""
        rPr = run._element.get_or_add_rPr()
        bidi = OxmlElement('w:rtl')
        rPr.append(bidi)

    def _add_hyperlink(self, paragraph, text, url):
        """Add a hyperlink to a paragraph."""
        try:
            # Based on python-docx documentation for adding hyperlinks
            from docx.oxml.shared import OxmlElement, qn
            
            # Create the hyperlink relationship
            r_id = paragraph.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
            
            # Create hyperlink element
            hyperlink = OxmlElement('w:hyperlink')
            hyperlink.set(qn('r:id'), r_id)
            hyperlink.set(qn('w:history'), '1')
            
            # Create new run for hyperlink
            new_run = OxmlElement('w:r')
            rPr = OxmlElement('w:rPr')
            
            # Add style to make it look like a hyperlink
            c = OxmlElement('w:color')
            c.set(qn('w:val'), '0000FF')
            rPr.append(c)
            
            u = OxmlElement('w:u')
            u.set(qn('w:val'), 'single')
            rPr.append(u)
            
            new_run.append(rPr)
            
            # Add text to run
            t = OxmlElement('w:t')
            t.text = text
            new_run.append(t)
            
            # Add run to hyperlink
            hyperlink.append(new_run)
            
            # Add hyperlink to paragraph
            p = paragraph._p
            p.append(hyperlink)
            
            return hyperlink
        except Exception as e:
            logger.warning(f"Failed to add hyperlink: {str(e)}")
            return None

    def _apply_text_formatting(self, run, element):
        """Apply comprehensive text formatting based on element attributes and styles."""
        # Check direct attributes
        if element.name in ['strong', 'b'] or 'font-weight:' in element.get('style', ''):
            run.bold = True
            
        if element.name in ['em', 'i'] or 'font-style:italic' in element.get('style', ''):
            run.italic = True
            
        if element.name == 'u' or 'text-decoration:underline' in element.get('style', ''):
            run.underline = True
            
        if element.name in ['s', 'strike', 'del'] or 'text-decoration:line-through' in element.get('style', ''):
            run.font.strike = True
            
        # Check style attribute for more complex formatting
        style = element.get('style', '')
        
        # Font color
        color_patterns = [
            r'color\s*:\s*#?([0-9a-fA-F]{6})',  # Hex color
            r'color\s*:\s*rgb\((\d+),\s*(\d+),\s*(\d+)\)',  # RGB color
            r'color\s*:\s*([a-z]+)'  # Named color
        ]
        
        for pattern in color_patterns:
            color_match = re.search(pattern, style)
            if color_match:
                try:
                    if pattern == color_patterns[0]:  # Hex
                        hex_color = color_match.group(1)
                        r = int(hex_color[0:2], 16)
                        g = int(hex_color[2:4], 16)
                        b = int(hex_color[4:6], 16)
                        run.font.color.rgb = RGBColor(r, g, b)
                    elif pattern == color_patterns[1]:  # RGB
                        r = int(color_match.group(1))
                        g = int(color_match.group(2))
                        b = int(color_match.group(3))
                        run.font.color.rgb = RGBColor(r, g, b)
                    elif pattern == color_patterns[2]:  # Named color
                        color_name = color_match.group(1).lower()
                        # Simple color conversion for common colors
                        color_map = {
                            'black': (0, 0, 0),
                            'white': (255, 255, 255),
                            'red': (255, 0, 0),
                            'green': (0, 128, 0),
                            'blue': (0, 0, 255),
                            'yellow': (255, 255, 0),
                            'purple': (128, 0, 128),
                            'gray': (128, 128, 128)
                        }
                        if color_name in color_map:
                            r, g, b = color_map[color_name]
                            run.font.color.rgb = RGBColor(r, g, b)
                except Exception as e:
                    logger.warning(f"Failed to set color: {str(e)}")
                break
            
        # Font size
        size_patterns = [
            r'font-size\s*:\s*(\d+)pt',  # Point size
            r'font-size\s*:\s*(\d+)px',  # Pixel size
            r'font-size\s*:\s*(\d+(?:\.\d+)?)rem',  # Relative to root em size
            r'font-size\s*:\s*(\d+(?:\.\d+)?)em'  # Relative to parent em size
        ]
        
        for pattern in size_patterns:
            size_match = re.search(pattern, style)
            if size_match:
                try:
                    # Convert to points as needed
                    if 'pt' in pattern:
                        font_size = float(size_match.group(1))
                    elif 'px' in pattern:
                        font_size = float(size_match.group(1)) * 0.75  # Approximate px to pt conversion
                    elif 'rem' in pattern:
                        font_size = float(size_match.group(1)) * 12  # Assuming root em is 12pt
                    elif 'em' in pattern:
                        font_size = float(size_match.group(1)) * 12  # Assuming parent em is 12pt
                    run.font.size = Pt(font_size)
                except Exception as e:
                    logger.warning(f"Failed to set font size: {str(e)}")
                break
            
        # Font family
        font_match = re.search(r'font-family\s*:\s*([^;]+)', style)
        if font_match:
            try:
                font_family = font_match.group(1).strip().split(',')[0].strip("'\"")
                run.font.name = font_family
            except Exception as e:
                logger.warning(f"Failed to set font family: {str(e)}")
                
        # Background color/highlighting
        bg_color_match = re.search(r'background-color\s*:\s*#?([0-9a-fA-F]{6})', style)
        if bg_color_match:
            try:
                # We can't set exact RGB background in docx easily, so we'll use highlight
                # Convert to a highlight color index approximation
                hex_color = bg_color_match.group(1)
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                
                # Simple mapping to standard highlight colors
                # 0: black, 1: blue, 2: cyan, 3: green, 4: magenta, 5: red, 6: yellow, 7: white, 8: dark blue
                # 9: dark cyan, 10: dark green, 11: dark magenta, 12: dark red, 13: dark yellow, 14: dark gray, 15: light gray
                
                if r > 200 and g > 200 and b < 100:  # Yellow-ish
                    run.font.highlight_color = 6  # Yellow
                elif r > 200 and g < 100 and b < 100:  # Red-ish
                    run.font.highlight_color = 5  # Red
                elif r < 100 and g < 100 and b > 200:  # Blue-ish
                    run.font.highlight_color = 1  # Blue
                elif r < 100 and g > 200 and b < 100:  # Green-ish
                    run.font.highlight_color = 3  # Green
                else:
                    # Default to light gray for other colors
                    run.font.highlight_color = 15  # Light gray
            except Exception as e:
                logger.warning(f"Failed to set highlight color: {str(e)}")
        
        # Text transform
        transform_match = re.search(r'text-transform\s*:\s*([^;]+)', style)
        if transform_match:
            transform_value = transform_match.group(1).strip().lower()
            text = run.text
            
            try:
                if transform_value == 'uppercase':
                    run.text = text.upper()
                elif transform_value == 'lowercase':
                    run.text = text.lower()
                elif transform_value == 'capitalize':
                    run.text = text.title()
            except Exception as e:
                logger.warning(f"Failed to apply text transform: {str(e)}")

    def _process_table(self, doc, table_elem, is_rtl=False):
        """Process HTML tables with comprehensive structure and formatting preservation."""
        # Extract table header and body rows
        thead = table_elem.find('thead')
        tbody = table_elem.find('tbody')
        tfoot = table_elem.find('tfoot')
        
        rows = []
        header_rows = []
        footer_rows = []
        
        if thead:
            header_rows = thead.find_all('tr')
            rows.extend(header_rows)
        if tbody:
            rows.extend(tbody.find_all('tr'))
        if tfoot:
            footer_rows = tfoot.find_all('tr')
            rows.extend(footer_rows)
        
        # If there's no explicit thead or tbody, get all rows directly
        if not rows:
            rows = table_elem.find_all('tr')
        
        if not rows:
            return
            
        # Determine table dimensions and handle colspan/rowspan
        max_cols = 0
        for row in rows:
            cells = row.find_all(['td', 'th'])
            col_count = 0
            for cell in cells:
                colspan = int(cell.get('colspan', 1))
                col_count += colspan
            max_cols = max(max_cols, col_count)
            
        if max_cols == 0:
            return
            
        # Create the table
        table = doc.add_table(rows=len(rows), cols=max_cols)
        table.style = 'Table Grid'
        
        # Get table properties from style
        table_style = table_elem.get('style', '')
        
        # Table width
        width_match = re.search(r'width\s*:\s*(\d+)(px|%)', table_style)
        if width_match:
            value = float(width_match.group(1))
            unit = width_match.group(2)
            if unit == '%':
                # Set table width as percentage of page width
                table_width_pct = min(100, value) / 100  # Ensure max is 100%
                for cell in table.columns[0].cells:
                    cell.width = Inches(6 * table_width_pct)  # Assuming 6-inch content width
        
        # Table alignment
        if 'margin-left:auto' in table_style and 'margin-right:auto' in table_style:
            # Center-aligned table
            table_element = table._tbl
            table_element.set('w:jc', 'center')
        
        # Process each row
        for i, row in enumerate(rows):
            cells = row.find_all(['td', 'th'])
            
            # Handle case with fewer cells than max_cols
            if len(cells) < max_cols:
                # Fill remaining cells with empty content
                for j in range(len(cells), max_cols):
                    table.cell(i, j).text = ""
            
            # Process each cell
            current_col = 0
            for cell in cells:
                if current_col >= max_cols:
                    break
                    
                try:
                    table_cell = table.cell(i, current_col)
                    
                    # Clear the default paragraph
                    if table_cell.paragraphs:
                        p = table_cell.paragraphs[0]
                        # Remove any existing content
                        for run in p.runs:
                            p._element.remove(run._element)
                    else:
                        p = table_cell.add_paragraph()
                    
                    # Extract cell properties
                    cell_style = cell.get('style', '')
                    
                    # Process cell text with special handling for our patterns
                    self._process_table_cell_content(p, cell, current_col, is_rtl)
                    
                    # Handle colspan and rowspan
                    colspan = int(cell.get('colspan', 1))
                    rowspan = int(cell.get('rowspan', 1))
                    
                    # Apply colspan by merging with adjacent cells
                    if colspan > 1 and current_col + colspan - 1 < max_cols:
                        # Merge cells horizontally
                        for col in range(1, colspan):
                            if current_col + col < max_cols:
                                target_cell = table.cell(i, current_col + col)
                                table_cell.merge(target_cell)
                    
                    # Apply rowspan by merging with cells below
                    if rowspan > 1 and i + rowspan - 1 < len(rows):
                        # First apply colspan if needed to get the right bottom-right cell
                        bottom_cell = table.cell(i + rowspan - 1, current_col)
                        if colspan > 1:
                            bottom_right_cell = table.cell(i + rowspan - 1, current_col + colspan - 1)
                            bottom_cell = table_cell  # We need to recalculate after merges
                        
                        # Now merge vertically
                        for row_idx in range(i + 1, i + rowspan):
                            if row_idx < len(rows):
                                table_cell.merge(table.cell(row_idx, current_col))
                        
                    # Apply cell formatting
                    if cell.name == 'th':
                        # Make header cells bold
                        for paragraph in table_cell.paragraphs:
                            for run in paragraph.runs:
                                run.bold = True
                        self._set_cell_shading(table_cell, 'f2f2f2')  # Light gray for headers
                    
                    # Background color
                    bg_color_match = re.search(r'background-color\s*:\s*#?([0-9a-fA-F]{6})', cell_style)
                    if bg_color_match:
                        self._set_cell_shading(table_cell, bg_color_match.group(1))
                        
                    # Text alignment
                    align_match = re.search(r'text-align\s*:\s*([^;]+)', cell_style)
                    if align_match:
                        align_value = align_match.group(1).strip().lower()
                        for paragraph in table_cell.paragraphs:
                            if align_value == 'center':
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            elif align_value == 'right':
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                            elif align_value == 'justify':
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                    elif cell.get('align'):
                        align_value = cell.get('align').lower()
                        for paragraph in table_cell.paragraphs:
                            if align_value == 'center':
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            elif align_value == 'right':
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                        
                    # Vertical alignment
                    valign_match = re.search(r'vertical-align\s*:\s*([^;]+)', cell_style)
                    if valign_match:
                        valign_value = valign_match.group(1).strip().lower()
                        if valign_value == 'top':
                            table_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                        elif valign_value == 'middle' or valign_value == 'center':
                            table_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                        elif valign_value == 'bottom':
                            table_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
                    elif cell.get('valign'):
                        valign_value = cell.get('valign').lower()
                        if valign_value == 'top':
                            table_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                        elif valign_value == 'middle' or valign_value == 'center':
                            table_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                        elif valign_value == 'bottom':
                            table_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
                
                except Exception as e:
                    logger.warning(f"Error processing table cell: {str(e)}")
                
                # Increment current column position based on colspan
                current_col += int(cell.get('colspan', 1))
        
        # Return the table object
        return table

    def _process_table_cell_content(self, paragraph, cell, column_index, is_rtl=False):
        """Process a table cell with special handling for complex content."""
        # First check if cell has only text or nested elements
        if len(list(cell.children)) == 1 and isinstance(next(cell.children), NavigableString):
            # Single text node - simple case
            cell_text = self._clean_text(cell.get_text())
            if cell_text:
                run = paragraph.add_run(cell_text)
                if is_rtl:
                    self._set_run_rtl(run)
            return
            
        # More complex cell content - could have nested elements
        for child in cell.children:
            if isinstance(child, NavigableString):
                text = self._clean_text(child)
                if text:
                    run = paragraph.add_run(text)
                    if is_rtl:
                        self._set_run_rtl(run)
            elif child.name == 'br':
                paragraph.add_run().add_break()
            elif child.name in ['p', 'div']:
                # For block elements in cells, add a paragraph break if needed
                if paragraph.runs:
                    paragraph = paragraph.part.add_paragraph()
                self._process_text_content(paragraph, child, is_rtl)
            else:
                # Process other inline elements
                self._process_text_content(paragraph, child, is_rtl)

    def _set_cell_shading(self, cell, color_hex):
        """Set the background shading of a table cell."""
        cell_properties = cell._element.tcPr
        if cell_properties is None:
            cell_properties = OxmlElement('w:tcPr')
            cell._element.append(cell_properties)
        
        shading = OxmlElement('w:shd')
        shading.set(qn('w:fill'), color_hex)
        shading.set(qn('w:val'), 'clear')  # 'clear' for normal shading
        shading.set(qn('w:color'), 'auto')
        shading.set(qn('w:themeFill'), color_hex)
        cell_properties.append(shading)

    def _process_list(self, doc, list_elem, is_rtl=False):
        """Process HTML lists with nesting and full style preservation."""
        is_ordered = list_elem.name == 'ol'
        items = list_elem.find_all('li', recursive=False)
        
        # Get any custom list attributes
        list_style_type = None
        list_style = list_elem.get('style', '')
        list_style_match = re.search(r'list-style-type\s*:\s*([^;]+)', list_style)
        if list_style_match:
            list_style_type = list_style_match.group(1).strip().lower()
        
        # Track the current list level for nested lists
        level = 0
        parent_list = list_elem.parent
        while parent_list:
            if parent_list.name in ['ol', 'ul']:
                level += 1
            parent_list = parent_list.parent
            
        for i, item in enumerate(items):
            # Determine list style based on type and nesting level
            if is_ordered:
                style_name = 'Custom Number List' if level == 0 else f'Custom Number List {level+1}'
                if style_name not in doc.styles:
                    style_name = 'List Number'
            else:
                style_name = 'Custom Bullet List' if level == 0 else f'Custom Bullet List {level+1}'
                if style_name not in doc.styles:
                    style_name = 'List Bullet'
            
            # Create paragraph with list style
            p = doc.add_paragraph(style=style_name)
            if is_rtl:
                p.style = 'RTLParagraph'
                # Also set the paragraph direction to RTL
                p._p.get_or_add_pPr().append(OxmlElement('w:bidi'))
            
            # Process content
            self._process_text_content(p, item, is_rtl)
            
            # Handle nested lists
            for nested_list in item.find_all(['ul', 'ol'], recursive=False):
                # Increase indentation for nested lists
                self._process_list(doc, nested_list, is_rtl)

    def get_base64_docx(self, docx_data: bytes) -> str:
        """Convert DOCX data to a base64 string."""
        return base64.b64encode(docx_data).decode('utf-8')

# Create a singleton instance
docx_generator_service = DocxGeneratorService()