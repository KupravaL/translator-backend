import re
import base64
import aiohttp
from bs4 import BeautifulSoup
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

class PDFGeneratorService:
    def __init__(self):
        # Register fonts
        self._register_fonts()
        
        # Initialize styles
        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(
            name='RightAlign',
            parent=self.styles['Normal'],
            alignment=2
        ))
        self.styles.add(ParagraphStyle(
            name='CenterAlign',
            parent=self.styles['Normal'],
            alignment=1
        ))
        
    async def _fetch_font(self, url):
        """Fetch a font from URL."""
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"Failed to fetch font: {response.status}")
                return await response.read()
    
    def _register_fonts(self):
        """Register fonts for use in PDF."""
        try:
            # Register default font
            pdfmetrics.registerFont(TTFont('Arial', 'Arial.ttf'))
        except:
            # Use default fonts if custom fonts are not available
            pass
    
    async def generate_pdf(self, html_content: str, rtl: bool = False) -> bytes:
        """Generate PDF from HTML content."""
        # Register a Georgian or RTL font if needed
        if rtl:
            try:
                georgian_font_url = "https://cdn.jsdelivr.net/gh/googlefonts/noto-fonts/unhinted/ttf/NotoSansGeorgian/NotoSansGeorgian-Regular.ttf"
                font_data = await self._fetch_font(georgian_font_url)
                font_file = BytesIO(font_data)
                pdfmetrics.registerFont(TTFont('NotoSansGeorgian', font_file))
            except Exception as e:
                print(f"Failed to register RTL font: {str(e)}")
        
        # Parse HTML content
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Create PDF file
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )
        
        # List to hold PDF elements
        elements = []
        
        # Process HTML elements
        for element in soup.find_all(['h1', 'h2', 'h3', 'p', 'table']):
            tag_name = element.name
            
            if tag_name in ['h1', 'h2', 'h3']:
                # Handle headings
                style_name = {
                    'h1': 'Heading1',
                    'h2': 'Heading2',
                    'h3': 'Heading3'
                }.get(tag_name, 'Normal')
                
                elements.append(Paragraph(element.get_text(), self.styles[style_name]))
                elements.append(Spacer(1, 12))
                
            elif tag_name == 'p':
                # Handle paragraphs
                text = element.get_text().strip()
                if text:  # Skip empty paragraphs
                    style = self.styles['Normal']
                    
                    # Check for alignment
                    if 'text-align:right' in str(element) or 'text-align: right' in str(element):
                        style = self.styles['RightAlign']
                    elif 'text-align:center' in str(element) or 'text-align: center' in str(element):
                        style = self.styles['CenterAlign']
                    
                    elements.append(Paragraph(text, style))
                    elements.append(Spacer(1, 6))
                    
            elif tag_name == 'table':
                # Handle tables
                data = []
                rows = element.find_all('tr')
                
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    row_data = [cell.get_text().strip() for cell in cells]
                    if row_data:  # Skip empty rows
                        data.append(row_data)
                
                if data:  # Skip empty tables
                    # Create table
                    table = Table(data)
                    
                    # Add table style
                    style = TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                        ('GRID', (0, 0), (-1, -1), 1, colors.black),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ])
                    table.setStyle(style)
                    
                    elements.append(table)
                    elements.append(Spacer(1, 12))
        
        # Build the PDF document
        doc.build(elements)
        
        # Get PDF content
        pdf_data = buffer.getvalue()
        buffer.close()
        
        return pdf_data
    
    def get_base64_pdf(self, pdf_data: bytes) -> str:
        """Convert PDF data to base64 string."""
        return base64.b64encode(pdf_data).decode('utf-8')

pdf_generator_service = PDFGeneratorService()