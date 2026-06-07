import os
import urllib.request
from pathlib import Path
from pypdf import PdfReader

BASE_DIR = Path(__file__).parent.parent
DOCS_DIR = BASE_DIR / "output" / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

def download_pdf(url: str, symbol: str, category: str) -> Path | None:
    """Download a PDF file from a URL to output/docs/<SYMBOL>/<category>.pdf with caching."""
    if not url:
        return None
    
    symbol_dir = DOCS_DIR / symbol.upper()
    symbol_dir.mkdir(parents=True, exist_ok=True)
    
    # Safe filename from URL
    filename = url.split("/")[-1]
    if not filename.lower().endswith(".pdf"):
        filename = f"{category.lower().replace(' ', '_')}.pdf"
        
    local_path = symbol_dir / filename
    
    if local_path.exists() and local_path.stat().st_size > 1000:
        print(f"     [PDF Cache] Using cached: {local_path.name}")
        return local_path
        
    print(f"     [PDF Download] Downloading: {url}")
    try:
        # User-agent header to avoid bot blocks
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=30) as response, open(local_path, 'wb') as out_file:
            out_file.write(response.read())
        print(f"     [PDF Download] Saved to: {local_path.name}")
        return local_path
    except Exception as e:
        print(f"     [PDF Download] Error downloading {url}: {e}")
        # Clean up partial downloads
        if local_path.exists():
            local_path.unlink()
        return None

def extract_pdf_text(file_path: Path, max_pages: int = 15) -> str:
    """Extract up to max_pages of text from a PDF file using pypdf."""
    if not file_path or not file_path.exists():
        return ""
        
    print(f"     [PDF Parser] Extracting text from {file_path.name}...")
    try:
        reader = PdfReader(file_path)
        text_pages = []
        total_pages = len(reader.pages)
        pages_to_read = min(total_pages, max_pages)
        
        for i in range(pages_to_read):
            page_text = reader.pages[i].extract_text() or ""
            text_pages.append(page_text)
            
        full_text = "\n\n--- PAGE BREAK ---\n\n".join(text_pages)
        print(f"     [PDF Parser] Extracted {len(full_text)} characters from {pages_to_read}/{total_pages} pages")
        return full_text
    except Exception as e:
        print(f"     [PDF Parser] Error parsing {file_path.name}: {e}")
        return ""

def process_knowledge_base_pdfs(knowledge_base_data: list, symbol: str) -> dict:
    """
    Download and extract text from the latest Investor Presentation,
    Concall Transcript, or Annual Report (fallback).
    """
    extracted_data = {}
    
    if not knowledge_base_data:
        return extracted_data
        
    # Standard knowledge base table structure has one table containing rows of documents
    # Table rows are typically: ['Annual Report', ...], ['Investor Presentation', ...], ['Conference Call', ...]
    # We parsed it into a list of tables. Each table has 'headers' and 'rows'.
    # A cell containing links is structured as: {"text": "Mar Jun", "links": [{"text": "Mar", "href": "url"}]}
    
    latest_presentation = None
    latest_concall = None
    latest_annual_report = None
    
    for table_data in knowledge_base_data:
        for row in table_data.get("rows", []):
            if not row:
                continue
            row_name = ""
            if isinstance(row[0], dict):
                row_name = row[0].get("text", "")
            else:
                row_name = str(row[0])
                
            row_name_lower = row_name.lower()
            
            # Extract links from all cells in the row
            all_links = []
            for cell in row[1:]:
                if isinstance(cell, dict) and cell.get("links"):
                    all_links.extend(cell["links"])
                    
            if not all_links:
                continue
                
            # Filter and sort links to find the latest
            if "presentation" in row_name_lower:
                latest_presentation = all_links[0]
            elif "conference call" in row_name_lower or "con call" in row_name_lower:
                latest_concall = all_links[0]
            elif "annual report" in row_name_lower:
                latest_annual_report = all_links[0]
                
    # Download and parse based on our fallback logic
    # Priority 1: Concall transcript (most fresh forward-looking guidance)
    # Priority 2: Investor Presentation (great visual guidance and expansion details)
    # Priority 3: Annual Report MD&A (fallback if no recent concalls/presentations)
    
    downloaded = False
    
    # 1. Download and parse Concall
    if latest_concall:
        path = download_pdf(latest_concall["href"], symbol, "concall")
        if path:
            text = extract_pdf_text(path, max_pages=15)
            if text:
                extracted_data["concall"] = {
                    "filename": path.name,
                    "url": latest_concall["href"],
                    "text": text
                }
                downloaded = True
                
    # 2. Download and parse Investor Presentation
    if latest_presentation:
        path = download_pdf(latest_presentation["href"], symbol, "presentation")
        if path:
            text = extract_pdf_text(path, max_pages=15)
            if text:
                extracted_data["presentation"] = {
                    "filename": path.name,
                    "url": latest_presentation["href"],
                    "text": text
                }
                downloaded = True
                
    # 3. Fallback: Annual Report (if no concall or presentation was processed)
    if not downloaded and latest_annual_report:
        print("     [PDF Handler] No concall or presentation downloaded. Falling back to Annual Report...")
        path = download_pdf(latest_annual_report["href"], symbol, "annual_report")
        if path:
            text = extract_pdf_text(path, max_pages=20)
            if text:
                extracted_data["annual_report"] = {
                    "filename": path.name,
                    "url": latest_annual_report["href"],
                    "text": text
                }
                
    return extracted_data
