"""
CloakBrowser API Server - Stealth browser for bot detection bypass
"""

import asyncio
import base64
import json
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

# CloakBrowser imports
from cloakbrowser import AsyncCloakBrowser


class TabCreate(BaseModel):
    userId: str
    sessionKey: Optional[str] = None
    url: str


class TabAction(BaseModel):
    userId: str


class ClickAction(BaseModel):
    userId: str
    selector: str


class TypeAction(BaseModel):
    userId: str
    selector: str
    text: str
    pressEnter: Optional[bool] = False


# Global state
browsers: dict = {}  # userId -> browser
pages: dict = {}     # tabId -> { page, userId, url }


async def get_browser(user_id: str):
    """Get or create a browser for a user"""
    if user_id not in browsers:
        cb = AsyncCloakBrowser()
        browser = await cb.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--single-process',
            ]
        )
        browsers[user_id] = browser
    return browsers[user_id]


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Cleanup
    for browser in browsers.values():
        try:
            await browser.close()
        except:
            pass


app = FastAPI(title="CloakBrowser API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "engine": "cloakbrowser",
        "activeTabs": len(pages),
        "activeSessions": len(browsers)
    }


@app.post("/tabs")
async def create_tab(tab: TabCreate):
    try:
        browser = await get_browser(tab.userId)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        tab_id = str(uuid.uuid4())
        await page.goto(tab.url, wait_until="domcontentloaded", timeout=30000)
        
        pages[tab_id] = {
            "page": page,
            "context": context,
            "userId": tab.userId,
            "url": tab.url
        }
        
        return {"tabId": tab_id, "url": tab.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tabs/{tab_id}/snapshot")
async def get_snapshot(tab_id: str, userId: str):
    if tab_id not in pages:
        raise HTTPException(status_code=404, detail="Tab not found")
    
    try:
        page = pages[tab_id]["page"]
        title = await page.title()
        content = await page.content()
        url = page.url
        
        # Get text content
        text_content = await page.evaluate("() => document.body.innerText")
        
        return {
            "url": url,
            "title": title,
            "snapshot": text_content[:5000],
            "content_length": len(content),
            "totalChars": len(text_content)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tabs/{tab_id}/screenshot")
async def get_screenshot(tab_id: str, userId: str):
    if tab_id not in pages:
        raise HTTPException(status_code=404, detail="Tab not found")
    
    try:
        page = pages[tab_id]["page"]
        screenshot = await page.screenshot(full_page=False)
        screenshot_b64 = base64.b64encode(screenshot).decode()
        
        return {"screenshot": screenshot_b64}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/tabs/{tab_id}")
async def close_tab(tab_id: str, userId: str):
    if tab_id not in pages:
        raise HTTPException(status_code=404, detail="Tab not found")
    
    try:
        tab_info = pages[tab_id]
        await tab_info["context"].close()
        del pages[tab_id]
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tabs/{tab_id}/click")
async def click_element(tab_id: str, action: ClickAction):
    if tab_id not in pages:
        raise HTTPException(status_code=404, detail="Tab not found")
    
    try:
        page = pages[tab_id]["page"]
        await page.click(action.selector)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tabs/{tab_id}/type")
async def type_text(tab_id: str, action: TypeAction):
    if tab_id not in pages:
        raise HTTPException(status_code=404, detail="Tab not found")
    
    try:
        page = pages[tab_id]["page"]
        await page.fill(action.selector, action.text)
        if action.pressEnter:
            await page.press(action.selector, "Enter")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
