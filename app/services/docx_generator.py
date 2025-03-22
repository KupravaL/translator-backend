import io
import base64
import re
import logging
from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.shared import Pt, Inches, RGBColor, Cm, Twips
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.table import _Cell, Table
import math

logger = logging.getLogger(__name__)

class DocxGeneratorService:
    def __init__(self):
        # Initialize standard and specialty fonts
        self._register_fonts()
        
        # Initialize styles
        self.styles = {}
        
        # Keep track of document structure
        self.current_section = None
        self.section_stack = []
        
    def _register_fonts(self):
        """Register standard and specialty fonts for use in PDF."""
        try:
            # Register default fonts if needed
            pass
        except Exception as e:
            logger.warning(f"Error initializing fonts: {str(e)}")
    
    def generate_docx(self, html_content: str) -> bytes:
        """Convert HTML to DOCX with comprehensive structure and style preservation."""
        try:
            # Create document
            doc = Document()
            
            # Configure default paragraph and document settings
            style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            style.paragraph_format.space_after = Pt(8)
            
            # Add custom styles
            self._add_custom_styles(doc)
            
            # Set document margins
            sections = doc.sections
            for section in sections:
                section.left_margin = Inches(1)
                section.right_margin = Inches(1)
                section.top_margin = Inches(1)
                section.bottom_margin = Inches(1)
            
            # Clean up the HTML content
            html_content = self._clean_html_content(html_content)
            
            # Parse HTML
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove meta and script tags
            for tag in soup.find_all(['meta', 'script']):
                tag.decompose()
            
            # Extract style tags for reference
            styles = soup.find_all('style')
            css_content = "\n".join([style.string for style in styles if style.string])
            
            # Process document structure - analyze document organization
            # This step helps maintain proper document flow
            document_structure = self._analyze_document_structure(soup)
            
            # Process document based on structure
            if document_structure:
                for section_type, element in document_structure:
                    if section_type == 'heading':
                        level = int(element.name[1])
                        heading = doc.add_heading(level=level)
                        self._process_text_content(heading, element)
                    elif section_type == 'article':
                        # Handle article sections properly
                        article_title = element.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                        if article_title:
                            level = int(article_title.name[1])
                            heading = doc.add_heading(level=level)
                            self._process_text_content(heading, article_title)
                        
                        # Process article content (excluding the title)
                        for content in element.children:
                            if isinstance(content, Tag) and content.name not in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                                self._process_element(doc, content, css_content)
                    elif section_type == 'table':
                        self._process_table(doc, element, css_content)
                    elif section_type == 'text':
                        para = doc.add_paragraph()
                        self._process_text_content(para, element)
                    elif section_type == 'page_break':
                        doc.add_page_break()
                    else:
                        # Process other sections
                        self._process_element(doc, element, css_content)
            else:
                # Fallback to basic processing if structure analysis fails
                document_div = soup.find('div', class_='document')
                
                if document_div:
                    # Handle multi-page documents
                    for i, page_div in enumerate(document_div.find_all('div', class_='page')):
                        # Process each page content
                        self._process_content(doc, page_div, css_content)
                        
                        # Add page break between pages
                        if i < len(document_div.find_all('div', class_='page')) - 1:
                            doc.add_page_break()
                else:
                    # No document structure found, process entire content
                    self._process_content(doc, soup, css_content)
            
            # Add headers and footers if present
            self._add_headers_and_footers(doc, soup)
            
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

    def _analyze_document_structure(self, soup):
        """
        Analyze the document structure to maintain proper flow and hierarchy.
        Returns a list of (section_type, element) tuples for ordered processing.
        """
        structure = []
        
        # Get document container if it exists
        document_div = soup.find('div', class_='document')
        
        if document_div:
            # Handle multi-page documents
            pages = document_div.find_all('div', class_='page')
            
            for page_idx, page in enumerate(pages):
                # Extract main components from each page
                page_elements = self._extract_page_structure(page)
                structure.extend(page_elements)
                
                # Add page break if not the last page
                if page_idx < len(pages) - 1:
                    structure.append(('page_break', None))
        else:
            # No document structure, process as single page
            structure = self._extract_page_structure(soup)
        
        return structure
        
    def _extract_page_structure(self, page_soup):
        """Extract the elements from a page in their logical order."""
        elements = []
        
        # Process headings first - they define document structure
        headings = page_soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
        
        # Extract articles and sections
        articles = page_soup.find_all('article')
        sections = page_soup.find_all('section')
        
        # Extract tables
        tables = page_soup.find_all('table')
        
        # Now build the page structure in logical order
        
        # First pass: get all direct children of the page in order
        # This preserves the natural flow of content
        if isinstance(page_soup, Tag):
            for child in page_soup.children:
                if isinstance(child, NavigableString):
                    if child.strip():
                        elements.append(('text', child))
                elif child.name:
                    if child.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                        elements.append(('heading', child))
                    elif child.name == 'article':
                        elements.append(('article', child))
                    elif child.name == 'section':
                        elements.append(('article', child))  # Treat sections similarly to articles
                    elif child.name == 'table':
                        elements.append(('table', child))
                    elif child.name == 'p':
                        elements.append(('text', child))
                    elif child.name in ['div', 'span']:
                        # Check if it contains meaningful content
                        if child.get_text().strip():
                            elements.append(('container', child))
                    elif child.name not in ['style', 'script', 'meta', 'link']:
                        elements.append(('element', child))
        
        return elements

    def _clean_html_content(self, html_content):
        """Clean up HTML content for processing."""
        # Remove DOCTYPE declarations, comments, and XML declarations
        html_content = re.sub(r'<!DOCTYPE[^>]*>', '', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)
        html_content = re.sub(r'<\?xml[^>]*\?>', '', html_content)
        html_content = html_content.replace('&nbsp;', ' ')
        
        # Fix any escaped HTML syntax
        if html_content.startswith('"') and html_content.endswith('"'):
            html_content = html_content[1:-1]
        
        # Fix escaped quotes and other characters
        html_content = html_content.replace('\\"', '"')
        html_content = html_content.replace('\\n', '\n')
        
        return html_content
    
    def _add_custom_styles(self, doc):
        """Add custom styles to the document for better formatting."""
        # Add heading styles
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
        
        # Add styles for special text blocks
        if 'ArticleText' not in doc.styles:
            style = doc.styles.add_style('ArticleText', 1)
            style.base_style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            style.paragraph_format.first_line_indent = Inches(0.25)
            style.paragraph_format.space_after = Pt(8)
        
        # Add styles for RTL text
        if 'RTLParagraph' not in doc.styles:
            style = doc.styles.add_style('RTLParagraph', 1)
            style.base_style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            
            # Set RTL text direction using XML
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
    
    def _process_content(self, doc, parent_element, css_content=None):
        """Process all content within the parent element in order."""
        # Find all direct children that need processing
        for child in parent_element.children:
            # Skip empty strings and irrelevant elements
            if isinstance(child, NavigableString):
                if child.strip():
                    para = doc.add_paragraph()
                    para.add_run(self._clean_text(child))
            elif child.name and child.name not in ['meta', 'script', 'style', 'link']:
                self._process_element(doc, child, css_content)

    def _process_element(self, doc, element, css_content=None):
        """Process an HTML element based on its type with improved structure handling."""
        if element is None:
            return
            
        if isinstance(element, NavigableString):
            if element.strip():
                para = doc.add_paragraph()
                para.add_run(self._clean_text(element))
            return
            
        # Skip processing certain elements
        if element.name in ['meta', 'script', 'style', 'link']:
            return
        
        # Get style information
        inline_style = element.get('style', '')
        css_styles = {}
        
        # Check for RTL text direction
        is_rtl = False
        if 'direction:rtl' in inline_style or 'direction: rtl' in inline_style:
            is_rtl = True
        
        # Process based on element type
        if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            # Process headings with proper level
            level = int(element.name[1])
            heading = doc.add_heading(level=level)
            self._process_text_content(heading, element, is_rtl=is_rtl)
            
            # Apply custom styling
            style_name = f'CustomHeading{level}'
            if style_name in doc.styles:
                heading.style = style_name
                
        elif element.name == 'p':
            # Process paragraphs
            para = doc.add_paragraph()
            
            # Apply paragraph class-based styling
            if element.get('class'):
                if 'text-content' in element.get('class'):
                    para.style = 'ArticleText'
                    
            # Set RTL style if needed
            if is_rtl:
                para.style = 'RTLParagraph'
                
            # Process paragraph content
            self._process_text_content(para, element, is_rtl=is_rtl)
            
            # Apply alignment from style
            self._apply_paragraph_alignment(para, element)
            
        elif element.name == 'article':
            # Process articles - important for legal documents
            # Articles typically contain heading + paragraphs
            
            # First find heading if present
            article_heading = element.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
            if article_heading:
                level = int(article_heading.name[1])
                heading = doc.add_heading(level=level)
                self._process_text_content(heading, article_heading)
            
            # Then process remaining content
            for content in element.children:
                if content != article_heading:  # Skip the heading we already processed
                    if isinstance(content, NavigableString):
                        if content.strip():
                            para = doc.add_paragraph()
                            para.add_run(self._clean_text(content))
                    elif content.name:
                        self._process_element(doc, content, css_content)
        
        elif element.name == 'section':
            # Process sections similar to articles
            section_heading = element.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
            if section_heading:
                level = int(section_heading.name[1])
                heading = doc.add_heading(level=level)
                self._process_text_content(heading, section_heading)
            
            # Process section content
            for content in element.children:
                if content != section_heading:
                    if isinstance(content, NavigableString):
                        if content.strip():
                            para = doc.add_paragraph()
                            para.add_run(self._clean_text(content))
                    elif content.name:
                        self._process_element(doc, content, css_content)
                        
        elif element.name == 'table':
            # Process tables
            self._process_table(doc, element, is_rtl=is_rtl)
            
        elif element.name in ['ul', 'ol']:
            # Process lists
            self._process_list(doc, element, is_rtl=is_rtl)
            
        elif element.name == 'div':
            # Process div containers
            
            # Check if this is a section with a specific class
            class_value = element.get('class', [])
            if isinstance(class_value, str):
                class_value = [class_value]
                
            if 'text-content' in class_value:
                # For text-content sections, process as a paragraph
                p = doc.add_paragraph()
                if is_rtl:
                    p.style = 'RTLParagraph'
                self._process_text_content(p, element, is_rtl=is_rtl)
            else:
                # Process child elements
                self._process_content(doc, element, css_content)
                
        elif element.name == 'br':
            # In standalone context, add a paragraph
            doc.add_paragraph()
            
        elif element.name in ['span', 'strong', 'em', 'b', 'i', 'u', 's', 'strike']:
            # For inline elements that appear at top level, wrap in paragraph
            para = doc.add_paragraph()
            if is_rtl:
                para.style = 'RTLParagraph'
            self._process_text_content(para, element, is_rtl=is_rtl)
            
        elif element.name == 'a':
            # Handle hyperlinks
            para = doc.add_paragraph()
            if is_rtl:
                para.style = 'RTLParagraph'
            self._process_text_content(para, element, is_rtl=is_rtl)
            
        elif element.name in ['pre', 'code']:
            # Handle code blocks
            para = doc.add_paragraph()
            run = para.add_run(self._clean_text(element.get_text()))
            run.font.name = 'Courier New'
            run.font.size = Pt(9)
            
        else:
            # For other elements, try to process children
            if any(child.name for child in element.children if isinstance(child, Tag)):
                self._process_content(doc, element, css_content)
            else:
                # Element only has text content
                text = self._clean_text(element.get_text())
                if text:
                    para = doc.add_paragraph()
                    para.add_run(text)

    def _clean_text(self, text):
        """Clean and normalize text content."""
        if text is None:
            return ""
        
        # Replace literal line breaks with spaces
        text = text.replace('\\n', ' ')
        
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()

    def _process_text_content(self, paragraph, element, is_rtl=False):
        """Process the text content of an element including inline formatting."""
        if element is None:
            return
            
        if isinstance(element, NavigableString):
            # Clean text and add as run
            text = self._clean_text(element)
            if text:
                run = paragraph.add_run(text)
                if is_rtl:
                    self._set_run_rtl(run)
            return
        
        # Check if element has direct text
        if element.string and element.string.strip():
            text = self._clean_text(element.string)
            run = paragraph.add_run(text)
            if is_rtl:
                self._set_run_rtl(run)
            self._apply_text_formatting(run, element)
            return
            
        # Process all children
        for child in element.children:
            if isinstance(child, NavigableString):
                text = self._clean_text(child)
                if text:
                    run = paragraph.add_run(text)
                    if is_rtl:
                        self._set_run_rtl(run)
                    self._apply_text_formatting(run, element)  # Apply parent formatting to text
            elif child.name == 'br':
                # Handle line breaks properly
                run = paragraph.add_run()
                run.add_break(WD_BREAK.LINE)
            elif child.name in ['strong', 'b']:
                # Bold text
                text = self._clean_text(child.get_text())
                run = paragraph.add_run(text)
                run.bold = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name in ['em', 'i']:
                # Italic text
                text = self._clean_text(child.get_text())
                run = paragraph.add_run(text)
                run.italic = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name == 'u':
                # Underlined text
                text = self._clean_text(child.get_text())
                run = paragraph.add_run(text)
                run.underline = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name in ['s', 'strike', 'del']:
                # Strikethrough text
                text = self._clean_text(child.get_text())
                run = paragraph.add_run(text)
                run.font.strike = True
                if is_rtl:
                    self._set_run_rtl(run)
                self._apply_text_formatting(run, child)
            elif child.name == 'a':
                # Hyperlinks
                text = self._clean_text(child.get_text())
                href = child.get('href', '')
                
                if text:
                    # Add hyperlink if possible, or styled text if not
                    try:
                        self._add_hyperlink(paragraph, text, href or "#")
                    except:
                        run = paragraph.add_run(text)
                        run.underline = True
                        run.font.color.rgb = RGBColor(0, 0, 255)
                        if is_rtl:
                            self._set_run_rtl(run)
            elif child.name == 'span':
                # Process spans with style info
                self._process_text_content(paragraph, child, is_rtl)
            else:
                # Process other elements recursively
                self._process_text_content(paragraph, child, is_rtl)

    def _set_run_rtl(self, run):
        """Set RTL text direction for a run."""
        try:
            rPr = run._element.get_or_add_rPr()
            rtl = OxmlElement('w:rtl')
            rtl.set(qn('w:val'), "1")
            rPr.append(rtl)
        except Exception as e:
            logger.warning(f"Failed to set RTL: {str(e)}")

    def _apply_paragraph_alignment(self, paragraph, element):
        """Apply text alignment to a paragraph based on HTML attributes and styles."""
        # Check for alignment in style attribute
        style = element.get('style', '')
        align_match = re.search(r'text-align\s*:\s*([^;]+)', style)
        
        # Check direct alignment attribute
        align_attr = element.get('align')
        
        # Set alignment based on found values
        if align_match:
            align_value = align_match.group(1).strip().lower()
            if align_value == 'center':
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif align_value == 'right':
                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            elif align_value == 'justify':
                paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            elif align_value == 'left':
                paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        elif align_attr:
            align_value = align_attr.lower()
            if align_value == 'center':
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif align_value == 'right':
                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            elif align_value == 'justify':
                paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            elif align_value == 'left':
                paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT

    def _apply_text_formatting(self, run, element):
        """Apply text formatting to a run based on HTML style attributes."""
        # Apply base formatting from element name
        if element.name in ['strong', 'b']:
            run.bold = True
        if element.name in ['em', 'i']:
            run.italic = True
        if element.name == 'u':
            run.underline = True
        if element.name in ['s', 'strike', 'del']:
            run.font.strike = True
        if element.name == 'sub':
            run.font.subscript = True
        if element.name == 'sup':
            run.font.superscript = True
            
        # Apply style attribute formatting
        style = element.get('style', '')
        
        # Font weight
        if 'font-weight:' in style:
            weight_match = re.search(r'font-weight\s*:\s*([^;]+)', style)
            if weight_match:
                weight = weight_match.group(1).strip().lower()
                if weight in ['bold', '700', '800', '900']:
                    run.bold = True
        
        # Font style
        if 'font-style:' in style:
            style_match = re.search(r'font-style\s*:\s*([^;]+)', style)
            if style_match:
                font_style = style_match.group(1).strip().lower()
                if font_style == 'italic':
                    run.italic = True
        
        # Text decoration
        if 'text-decoration:' in style:
            decoration_match = re.search(r'text-decoration\s*:\s*([^;]+)', style)
            if decoration_match:
                decoration = decoration_match.group(1).strip().lower()
                if 'underline' in decoration:
                    run.underline = True
                if 'line-through' in decoration:
                    run.font.strike = True
        
        # Font color
        color_match = re.search(r'color\s*:\s*([^;]+)', style)
        if color_match:
            color_value = color_match.group(1).strip().lower()
            try:
                # Handle hex colors
                if color_value.startswith('#'):
                    hex_color = color_value.lstrip('#')
                    if len(hex_color) == 6:
                        r = int(hex_color[0:2], 16)
                        g = int(hex_color[2:4], 16)
                        b = int(hex_color[4:6], 16)
                        run.font.color.rgb = RGBColor(r, g, b)
                # Handle basic named colors
                elif color_value in ['black', 'white', 'red', 'green', 'blue', 'yellow']:
                    if color_value == 'black':
                        run.font.color.rgb = RGBColor(0, 0, 0)
                    elif color_value == 'white':
                        run.font.color.rgb = RGBColor(255, 255, 255)
                    elif color_value == 'red':
                        run.font.color.rgb = RGBColor(255, 0, 0)
                    elif color_value == 'green':
                        run.font.color.rgb = RGBColor(0, 128, 0)
                    elif color_value == 'blue':
                        run.font.color.rgb = RGBColor(0, 0, 255)
                    elif color_value == 'yellow':
                        run.font.color.rgb = RGBColor(255, 255, 0)
            except Exception as e:
                logger.warning(f"Failed to set color: {str(e)}")
        
        # Font size
        size_match = re.search(r'font-size\s*:\s*(\d+)(?:px|pt|em|rem)?', style)
        if size_match:
            try:
                size_value = int(size_match.group(1))
                if 'px' in style:
                    # Convert pixels to points (approximate)
                    size_value = int(size_value * 0.75)
                run.font.size = Pt(size_value)
            except Exception as e:
                logger.warning(f"Failed to set font size: {str(e)}")
        
        # Font family
        font_match = re.search(r'font-family\s*:\s*([^;]+)', style)
        if font_match:
            try:
                font_value = font_match.group(1).strip()
                # Extract first font in the list and remove quotes
                font_name = re.sub(r'^[\'"]|[\'"]$', '', font_value.split(',')[0].strip())
                run.font.name = font_name
            except Exception as e:
                logger.warning(f"Failed to set font family: {str(e)}")

    def _add_hyperlink(self, paragraph, text, url):
        """Add a hyperlink to a paragraph."""
        try:
            part = paragraph.part
            r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
            
            # Create hyperlink element
            hyperlink = OxmlElement('w:hyperlink')
            hyperlink.set(qn('r:id'), r_id)
            
            # Create run
            new_run = OxmlElement('w:r')
            rPr = OxmlElement('w:rPr')
            
            # Add style (blue and underlined)
            color = OxmlElement('w:color')
            color.set(qn('w:val'), '0000FF')
            rPr.append(color)
            
            underline = OxmlElement('w:u')
            underline.set(qn('w:val'), 'single')
            rPr.append(underline)
            
            new_run.append(rPr)
            
            # Add text
            t = OxmlElement('w:t')
            t.text = text
            new_run.append(t)
            
            # Add run to hyperlink
            hyperlink.append(new_run)
            
            # Add hyperlink to paragraph
            paragraph._p.append(hyperlink)
            
            return hyperlink
        except Exception as e:
            logger.warning(f"Failed to add hyperlink: {str(e)}")
            # Fallback to styled text
            run = paragraph.add_run(text)
            run.font.color.rgb = RGBColor(0, 0, 255)
            run.underline = True
            return run

    def _process_table(self, doc, table_elem, css_content=None, is_rtl=False):
        """
        Process HTML tables with comprehensive structure and formatting preservation.
        Improved to handle complex tables with merged cells and proper styling.
        """
        # Extract header and body rows
        thead = table_elem.find('thead')
        tbody = table_elem.find('tbody')
        tfoot = table_elem.find('tfoot')
        
        # Get all rows in correct order
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
        
        # If no explicit structure, get all rows directly
        if not rows:
            rows = table_elem.find_all('tr')
        
        if not rows:
            return
            
        # Analyze table dimensions accounting for rowspan/colspan
        col_counts = []
        for row in rows:
            cells = row.find_all(['td', 'th'])
            col_count = 0
            for cell in cells:
                colspan = int(cell.get('colspan', 1))
                col_count += colspan
            col_counts.append(col_count)
        
        # Determine max columns
        max_cols = max(col_counts) if col_counts else 0
        if max_cols == 0:
            return
        
        # Create table
        table = doc.add_table(rows=len(rows), cols=max_cols)
        table.style = 'Table Grid'
        
        # Helper to create cell map for tracing merged areas
        cell_map = [[None for _ in range(max_cols)] for _ in range(len(rows))]
        
        # Process table styles
        table_style = table_elem.get('style', '')
        table_class = table_elem.get('class', [])
        if isinstance(table_class, str):
            table_class = [table_class]
        
        # Apply table width if specified
        width_match = re.search(r'width\s*:\s*(\d+)([%a-z]+)', table_style)
        if width_match:
            width_val = float(width_match.group(1))
            width_unit = width_match.group(2)
            
            if width_unit == '%':
                # Apply percentage of page width
                width_pct = min(100, width_val) / 100
                tbl_element = table._tbl
                tbl_width = OxmlElement('w:tblW')
                tbl_width.set(qn('w:w'), str(int(5000 * width_pct)))  # 5000 = 100%
                tbl_width.set(qn('w:type'), 'pct')
                tbl_pr = tbl_element.get_or_add_tblPr()
                tbl_pr.append(tbl_width)
        
        # Apply table alignment
        if 'margin-left:auto' in table_style and 'margin-right:auto' in table_style:
            # Center alignment
            tbl_element = table._tbl
            jc = OxmlElement('w:jc')
            jc.set(qn('w:val'), 'center')
            tbl_pr = tbl_element.get_or_add_tblPr()
            tbl_pr.append(jc)
        elif 'data-table' in table_class:
            # Common class for centered tables
            tbl_element = table._tbl
            jc = OxmlElement('w:jc')
            jc.set(qn('w:val'), 'center')
            tbl_pr = tbl_element.get_or_add_tblPr()
            tbl_pr.append(jc)
        
        # Process each row
        for row_idx, row in enumerate(rows):
            cells = row.find_all(['td', 'th'])
            
            # Track current column position accounting for merged cells
            current_col = 0
            
            # Process cells in this row
            for cell_idx, cell in enumerate(cells):
                # Skip positions already occupied by row-spanning cells from previous rows
                while current_col < max_cols and cell_map[row_idx][current_col] is not None:
                    current_col += 1
                
                # If we've run out of columns, break
                if current_col >= max_cols:
                    break
                
                try:
                    # Get colspan and rowspan
                    colspan = int(cell.get('colspan', 1))
                    rowspan = int(cell.get('rowspan', 1))
                    
                    # Get the cell from the Word table
                    table_cell = table.cell(row_idx, current_col)
                    
                    # Mark this cell and merged region in the cell map
                    for r in range(row_idx, min(row_idx + rowspan, len(rows))):
                        for c in range(current_col, min(current_col + colspan, max_cols)):
                            if r == row_idx and c == current_col:
                                cell_map[r][c] = "ORIGIN"  # This is the top-left cell
                            else:
                                cell_map[r][c] = "MERGED"  # This position is part of a merged cell
                    
                    # Clear existing content in the cell
                    for paragraph in table_cell.paragraphs:
                        for run in paragraph.runs:
                            run._element.getparent().remove(run._element)
                    
                    # Process cell style attributes
                    cell_style = cell.get('style', '')
                    
                    # Process cell content - key improvement for handling line breaks
                    cell_content = []
                    
                    # Check if cell has simple text or complex content
                    if len(list(cell.children)) == 1 and isinstance(next(cell.children), NavigableString):
                        # Single text node
                        cell_text = self._clean_text(cell.string)
                        if cell_text:
                            para = table_cell.paragraphs[0] if table_cell.paragraphs else table_cell.add_paragraph()
                            para.add_run(cell_text)
                    else:
                        # Complex content - may contain line breaks, formatting, etc.
                        # First check if content should be split on <br/> tags
                        if cell.find('br'):
                            # Handle <br/> tags by creating multiple paragraphs
                            contents = []
                            current_content = ""
                            
                            for item in cell.contents:
                                if isinstance(item, NavigableString):
                                    current_content += str(item)
                                elif item.name == 'br':
                                    contents.append(current_content.strip())
                                    current_content = ""
                                else:
                                    current_content += str(item)
                            
                            # Add any remaining content
                            if current_content.strip():
                                contents.append(current_content.strip())
                            
                            # Create a paragraph for each content piece
                            for i, content in enumerate(contents):
                                if i == 0 and table_cell.paragraphs:
                                    para = table_cell.paragraphs[0]
                                else:
                                    para = table_cell.add_paragraph()
                                
                                # Handle the possibility of HTML in content
                                try:
                                    soup = BeautifulSoup(f"<div>{content}</div>", 'html.parser')
                                    self._process_text_content(para, soup.div, is_rtl=is_rtl)
                                except:
                                    para.add_run(content)
                        else:
                            # Single paragraph but may have formatting
                            para = table_cell.paragraphs[0] if table_cell.paragraphs else table_cell.add_paragraph()
                            self._process_text_content(para, cell, is_rtl=is_rtl)
                    
                    # Apply cell formatting
                    
                    # Handle cell background color
                    bg_color_match = re.search(r'background-color\s*:\s*([^;]+)', cell_style)
                    if bg_color_match:
                        color_val = bg_color_match.group(1).strip()
                        # Extract hex color
                        if color_val.startswith('#'):
                            color_hex = color_val.lstrip('#')
                            self._set_cell_shading(table_cell, color_hex)
                        elif color_val.startswith('rgb'):
                            # Handle rgb() format
                            rgb_match = re.search(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', color_val)
                            if rgb_match:
                                r = int(rgb_match.group(1))
                                g = int(rgb_match.group(2))
                                b = int(rgb_match.group(3))
                                color_hex = f"{r:02x}{g:02x}{b:02x}"
                                self._set_cell_shading(table_cell, color_hex)
                    
                    # Special formatting for header cells
                    if cell.name == 'th':
                        # Make header cells bold
                        for paragraph in table_cell.paragraphs:
                            for run in paragraph.runs:
                                run.bold = True
                        
                        # Add light gray background if not already set
                        if not bg_color_match:
                            self._set_cell_shading(table_cell, 'f2f2f2')
                    
                    # Apply text alignment
                    align_match = re.search(r'text-align\s*:\s*([^;]+)', cell_style)
                    align_attr = cell.get('align')
                    
                    if align_match or align_attr:
                        align_value = None
                        if align_match:
                            align_value = align_match.group(1).strip().lower()
                        elif align_attr:
                            align_value = align_attr.lower()
                        
                        for paragraph in table_cell.paragraphs:
                            if align_value == 'center':
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            elif align_value == 'right':
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                            elif align_value == 'justify':
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                    
                    # Apply vertical alignment
                    valign_match = re.search(r'vertical-align\s*:\s*([^;]+)', cell_style)
                    valign_attr = cell.get('valign')
                    
                    if valign_match or valign_attr:
                        valign_value = None
                        if valign_match:
                            valign_value = valign_match.group(1).strip().lower()
                        elif valign_attr:
                            valign_value = valign_attr.lower()
                        
                        if valign_value == 'top':
                            table_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                        elif valign_value in ['middle', 'center']:
                            table_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                        elif valign_value == 'bottom':
                            table_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
                    
                    # Apply cell spanning
                    if colspan > 1:
                        # Merge cells horizontally
                        for i in range(1, colspan):
                            if current_col + i < max_cols:
                                try:
                                    table_cell.merge(table.cell(row_idx, current_col + i))
                                except Exception as e:
                                    logger.warning(f"Failed to merge cells horizontally: {str(e)}")
                    
                    if rowspan > 1:
                        # Merge cells vertically
                        for i in range(1, rowspan):
                            if row_idx + i < len(rows):
                                try:
                                    table_cell.merge(table.cell(row_idx + i, current_col))
                                except Exception as e:
                                    logger.warning(f"Failed to merge cells vertically: {str(e)}")
                    
                    # Update current column position
                    current_col += colspan
                    
                except Exception as e:
                    logger.warning(f"Error processing table cell: {str(e)}")
                    current_col += 1
        
        return table

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
        cell_properties.append(shading)

    def _process_list(self, doc, list_elem, is_rtl=False):
        """
        Process HTML lists with proper nesting and formatting.
        Improved to handle bullet and numbered lists correctly.
        """
        is_ordered = list_elem.name == 'ol'
        items = list_elem.find_all('li', recursive=False)
        
        # Get list style attributes
        list_style = list_elem.get('style', '')
        list_class = list_elem.get('class', [])
        if isinstance(list_class, str):
            list_class = [list_class]
        
        # Determine list level by counting parent lists
        level = 0
        parent = list_elem.parent
        while parent:
            if parent.name in ['ol', 'ul']:
                level += 1
            parent = parent.parent
        
        # Process list items
        for item in items:
            # Choose appropriate list style based on type and nesting
            if is_ordered:
                style_name = 'List Number'
            else:
                style_name = 'List Bullet'
            
            # Create a paragraph with list style
            p = doc.add_paragraph(style=style_name)
            
            # Apply RTL if needed
            if is_rtl:
                p._p.get_or_add_pPr().append(OxmlElement('w:bidi'))
            
            # Set the list level based on nesting
            if level > 0:
                p._p.get_or_add_pPr().get_or_add_numPr().get_or_add_ilvl().val = level
            
            # Process the content of the list item
            self._process_text_content(p, item, is_rtl=is_rtl)
            
            # Handle nested lists
            nested_lists = item.find_all(['ul', 'ol'], recursive=False)
            for nested_list in nested_lists:
                self._process_list(doc, nested_list, is_rtl=is_rtl)

    def _add_headers_and_footers(self, doc, soup):
        """Add headers and footers to the document if present in HTML."""
        # Find header and footer elements
        header_elem = soup.find('header')
        footer_elem = soup.find('footer')
        
        # Process header
        if header_elem:
            for section in doc.sections:
                header = section.header
                header_para = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
                self._process_text_content(header_para, header_elem)
                
                # Right-align the header text by default
                header_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        
        # Process footer
        if footer_elem:
            for section in doc.sections:
                footer = section.footer
                footer_para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
                self._process_text_content(footer_para, footer_elem)
                
                # Center-align the footer text by default
                footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def get_base64_docx(self, docx_data: bytes) -> str:
        """Convert DOCX data to a base64 string."""
        return base64.b64encode(docx_data).decode('utf-8')

# Create a singleton instance
docx_generator_service = DocxGeneratorService()