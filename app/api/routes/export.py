from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel
from app.core.auth import get_current_user
from app.services.pdf_generator import pdf_generator_service
from app.services.docx_generator import docx_generator_service
from typing import Optional

router = APIRouter()

class DocumentExportRequest(BaseModel):
    text: str
    fileName: Optional[str] = None

@router.post("/pdf")
async def export_to_pdf(
    request: DocumentExportRequest,
    current_user: str = Depends(get_current_user)
):
    """Export HTML content to PDF."""
    try:
        # Generate PDF
        file_name = request.fileName or "document.pdf"
        is_rtl = 'rtl' in request.text.lower() or 'direction:rtl' in request.text.lower()
        
        pdf_data = await pdf_generator_service.generate_pdf(request.text, rtl=is_rtl)
        base64_data = pdf_generator_service.get_base64_pdf(pdf_data)
        
        return {
            "pdfData": base64_data,
            "fileName": file_name,
            "message": "PDF generated successfully"
        }
        
    except Exception as e:
        print(f"PDF generation error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF generation failed: {str(e)}"
        )

@router.post("/docx")
async def export_to_docx(
    request: DocumentExportRequest,
    current_user: str = Depends(get_current_user)
):
    """Export HTML content to DOCX."""
    try:
        # Generate DOCX
        file_name = request.fileName or "document.docx"
        
        docx_data = docx_generator_service.generate_docx(request.text)
        base64_data = docx_generator_service.get_base64_docx(docx_data)
        
        return {
            "docxData": base64_data,
            "fileName": file_name,
            "message": "Document generated successfully"
        }
        
    except Exception as e:
        print(f"DOCX generation error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document generation failed: {str(e)}"
        )