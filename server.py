"""
CloakBrowser API Server - Stealth browser for bot detection bypass
"""

import asyncio
import base64
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

from cloakbrowser import launch_async


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
browsers: dict = {}
pages: dict = {}


async def get_browser(user_id: str):
    if user_id not in browsers:
        browser = await launch_async(
            headless=True,
            geoip=True,
            humanize=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
            ]
        )
        browsers[user_id] = browser
    return browsers[user_id]


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
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
            device_scale_factor=1,
        )
        page = await context.new_page()
        
        tab_id = str(uuid.uuid4())

        # First load: solve Cloudflare Turnstile challenge
        try:
            await page.goto(tab.url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass

        # Wait for Turnstile to solve (always reload regardless)
        for i in range(10):
            await page.wait_for_timeout(3000)

        # Reload with Turnstile cookie to load actual content + assets
        try:
            await page.goto(tab.url, wait_until="networkidle", timeout=30000)
        except Exception:
            pass

        # Wait for React SPA to hydrate
        for i in range(5):
            root_len = await page.evaluate("() => (document.getElementById('root')?.innerHTML?.length || 0)")
            if root_len > 500:
                break
            await page.wait_for_timeout(3000)
        
        await page.wait_for_timeout(2000)
        
        # Close sign-in modal if present
        try:
            close_btn = page.locator('button[aria-label="Close"], [data-testid="close-button"]')
            if await close_btn.count() > 0:
                await close_btn.first.click()
                await page.wait_for_timeout(1000)
            else:
                await page.mouse.click(10, 10)
                await page.wait_for_timeout(500)
        except Exception:
            pass
        
        pages[tab_id] = {
            "page": page,
            "context": context,
            "userId": tab.userId,
            "url": tab.url
        }
        
        return {"tabId": tab_id, "url": tab.url}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)[:500]}")


@app.get("/tabs/{tab_id}/snapshot")
async def get_snapshot(tab_id: str, userId: str):
    if tab_id not in pages:
        raise HTTPException(status_code=404, detail="Tab not found")
    
    try:
        page = pages[tab_id]["page"]
        title = await page.title()
        content = await page.content()
        url = page.url
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
        # Ensure page is still alive and rendered
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        screenshot = await page.screenshot(full_page=True, type="png")
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
