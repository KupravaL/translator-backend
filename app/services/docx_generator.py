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
from docx.oxml import OxmlElement, parse_xml
from docx.table import _Cell, Table
import math

logger = logging.getLogger(__name__)

class DocxGeneratorService:
    def __init__(self):
        # Initialize standard and specialty fonts
        self._register_fonts()
        
        # Initialize CSS parser and style cache
        self.css_styles = {}
        
        # Keep track of document structure
        self.current_section = None
        self.section_stack = []
        
    def _register_fonts(self):
        """Register standard and specialty fonts for use in DOCX."""
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
            
            # Parse CSS content and build style cache
            self._parse_css_styles(css_content)
            
            # Process document structure - analyze document organization
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
                        self._process_table(doc, element)
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
                        self._process_content(doc, page_div)
                        
                        # Add page break between pages
                        if i < len(document_div.find_all('div', class_='page')) - 1:
                            doc.add_page_break()
                else:
                    # No document structure found, process entire content
                    self._process_content(doc, soup)
            
            # Add headers and footers if present
            self._add_headers_and_footers(doc, soup)
            
            # Perform final cleanup operations
            self._cleanup_docx(doc)
            
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

    def _parse_css_styles(self, css_content):
        """Parse CSS content and build a style cache for elements."""
        if not css_content:
            return
            
        # Reset style cache
        self.css_styles = {}
        
        try:
            # Simple CSS parser for common selectors
            # Parse class selectors
            class_matches = re.finditer(r'\.([a-zA-Z0-9_-]+)\s*{([^}]+)}', css_content)
            for match in class_matches:
                class_name = match.group(1)
                style_content = match.group(2)
                style_dict = self._parse_style_attributes(style_content)
                self.css_styles[f'.{class_name}'] = style_dict
            
            # Parse element selectors
            element_matches = re.finditer(r'([a-zA-Z0-9_-]+)\s*{([^}]+)}', css_content)
            for match in element_matches:
                element_name = match.group(1)
                if element_name.startswith('.'):  # Skip class selectors already processed
                    continue
                style_content = match.group(2)
                style_dict = self._parse_style_attributes(style_content)
                self.css_styles[element_name] = style_dict
            
            # Parse combined selectors (e.g., ".data-table th")
            combined_matches = re.finditer(r'([a-zA-Z0-9_\-\.\s]+)\s*{([^}]+)}', css_content)
            for match in combined_matches:
                selector = match.group(1).strip()
                if selector.count(' ') > 0:  # Only process combined selectors
                    style_content = match.group(2)
                    style_dict = self._parse_style_attributes(style_content)
                    self.css_styles[selector] = style_dict
                    
        except Exception as e:
            logger.warning(f"Error parsing CSS: {str(e)}")

    def _parse_style_attributes(self, style_content):
        """Parse style attributes into a dictionary."""
        style_dict = {}
        style_attrs = re.finditer(r'([a-zA-Z\-]+)\s*:\s*([^;]+);?', style_content)
        for attr in style_attrs:
            property_name = attr.group(1).strip()
            property_value = attr.group(2).strip()
            style_dict[property_name] = property_value
        return style_dict

    def _get_element_styles(self, element, default_styles=None):
        """Get combined styles for an element from inline and CSS styles."""
        if default_styles is None:
            default_styles = {}
            
        combined_styles = default_styles.copy()
        
        # Apply CSS styles based on element type
        if element.name in self.css_styles:
            element_styles = self.css_styles[element.name]
            combined_styles.update(element_styles)
        
        # Apply CSS styles based on class
        if element.get('class'):
            classes = element.get('class')
            if isinstance(classes, str):
                classes = [classes]
                
            for class_name in classes:
                class_selector = f'.{class_name}'
                if class_selector in self.css_styles:
                    class_styles = self.css_styles[class_selector]
                    combined_styles.update(class_styles)
                
                # Check for combined selectors (e.g., ".data-table th")
                if element.parent:
                    for selector, styles in self.css_styles.items():
                        if ' ' in selector:
                            parts = selector.split(' ')
                            if len(parts) == 2:
                                parent_selector, child_selector = parts
                                parent_match = False
                                child_match = False
                                
                                # Check if parent matches
                                if parent_selector.startswith('.'):
                                    parent_class = parent_selector[1:]
                                    parent_classes = element.parent.get('class', [])
                                    if isinstance(parent_classes, str):
                                        parent_classes = [parent_classes]
                                    parent_match = parent_class in parent_classes
                                else:
                                    parent_match = element.parent.name == parent_selector
                                
                                # Check if child matches
                                if child_selector.startswith('.'):
                                    child_class = child_selector[1:]
                                    child_match = child_class in classes
                                else:
                                    child_match = element.name == child_selector
                                
                                if parent_match and child_match:
                                    combined_styles.update(styles)
        
        # Apply inline styles (highest priority)
        inline_style = element.get('style', '')
        if inline_style:
            style_dict = {}
            style_attrs = re.finditer(r'([a-zA-Z\-]+)\s*:\s*([^;]+);?', inline_style)
            for attr in style_attrs:
                property_name = attr.group(1).strip()
                property_value = attr.group(2).strip()
                style_dict[property_name] = property_value
            combined_styles.update(style_dict)
        
        return combined_styles

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
        
        # Add styles for table elements
        if 'TableHeader' not in doc.styles:
            style = doc.styles.add_style('TableHeader', 1)
            style.base_style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            style.font.bold = True
            
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
            
        # Add specific styles for legal document formatting
        if 'LegalArticle' not in doc.styles:
            style = doc.styles.add_style('LegalArticle', 1)
            style.base_style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.bold = True
            style.font.size = Pt(11)
            style.paragraph_format.space_before = Pt(12)
            style.paragraph_format.space_after = Pt(6)
            
        # Add centered text style
        if 'CenteredText' not in doc.styles:
            style = doc.styles.add_style('CenteredText', 1)
            style.base_style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(11)
            style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    def _process_content(self, doc, parent_element):
        """Process all content within the parent element in order."""
        # Find all direct children that need processing
        for child in parent_element.children:
            # Skip empty strings and irrelevant elements
            if isinstance(child, NavigableString):
                if child.strip():
                    para = doc.add_paragraph()
                    para.add_run(self._clean_text(child))
            elif child.name and child.name not in ['meta', 'script', 'style', 'link']:
                self._process_element(doc, child)

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
        
        # Get consolidated styles for this element
        element_styles = self._get_element_styles(element)
        
        # Check for RTL text direction
        is_rtl = False
        if element_styles.get('direction') == 'rtl':
            is_rtl = True
        
        # Process based on element type
        if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            # Process headings with proper level
            level = int(element.name[1])
            heading = doc.add_heading(level=level)
            
            # Apply text alignment from style
            text_align = element_styles.get('text-align')
            if text_align:
                if text_align == 'center':
                    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif text_align == 'right':
                    heading.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                elif text_align == 'justify':
                    heading.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                    
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
                classes = element.get('class')
                if isinstance(classes, str):
                    classes = [classes]
                
                if 'text-content' in classes:
                    para.style = 'ArticleText'
                    
            # Set RTL style if needed
            if is_rtl:
                para.style = 'RTLParagraph'
                
            # Process paragraph content
            self._process_text_content(para, element, is_rtl=is_rtl)
            
            # Apply alignment from style
            text_align = element_styles.get('text-align')
            if text_align:
                if text_align == 'center':
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif text_align == 'right':
                    para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                elif text_align == 'justify':
                    para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            
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
                        self._process_element(doc, content)
        
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
                        self._process_element(doc, content)
                        
        elif element.name == 'table':
            # Process tables with special attention to styles and structure
            self._process_table(doc, element)
            
        elif element.name in ['ul', 'ol']:
            # Process lists with improved nesting
            self._process_list(doc, element, is_rtl=is_rtl)
            
        elif element.name == 'div':
            # Process div containers with attention to class-based styling
            
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
                
                # Apply text alignment from style
                text_align = element_styles.get('text-align')
                if text_align:
                    if text_align == 'center':
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    elif text_align == 'right':
                        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    elif text_align == 'justify':
                        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            else:
                # Process child elements
                self._process_content(doc, element)
                
        elif element.name == 'br':
            # In standalone context, add a paragraph
            doc.add_paragraph()
            
        elif element.name in ['span', 'strong', 'em', 'b', 'i', 'u', 's', 'strike']:
            # For inline elements that appear at top level, wrap in paragraph
            para = doc.add_paragraph()
            if is_rtl:
                para.style = 'RTLParagraph'
            self._process_text_content(para, element, is_rtl=is_rtl)
            
            # Apply text alignment from style
            text_align = element_styles.get('text-align')
            if text_align:
                if text_align == 'center':
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif text_align == 'right':
                    para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                elif text_align == 'justify':
                    para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            
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
                self._process_content(doc, element)
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
        
        # Get element styles
        element_styles = self._get_element_styles(element)
        
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