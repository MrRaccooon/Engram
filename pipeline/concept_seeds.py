"""
Seed concept vocabulary for zero-shot CLIP tagging.

The list is intentionally broad and generated from curated primitives so the
initial concept space is large without hand-writing hundreds of prompts.
"""

from __future__ import annotations


_CATEGORIES: dict[str, list[str]] = {
    "code_editors": [
        "a code editor with source code",
        "python code in an editor",
        "javascript code in an editor",
        "typescript code in an editor",
        "a code diff or patch view",
        "a debugging session in an IDE",
        "an IDE with file explorer sidebar",
        "a code review screen",
        "a test output panel in an IDE",
        "a linter warning in code",
        "a stack trace in an editor",
        "a project workspace with multiple files",
    ],
    "terminals": [
        "a terminal with command line output",
        "a powershell terminal window",
        "a shell prompt waiting for input",
        "terminal output with an error message",
        "terminal output showing tests running",
        "terminal output showing package installation",
        "a build log in terminal",
        "a terminal with git commands",
        "a terminal showing docker logs",
        "a terminal with python traceback",
        "a terminal with npm error",
        "a terminal with compilation output",
    ],
    "browsers": [
        "a web browser page",
        "search results on a browser",
        "online documentation in a browser",
        "a github repository page",
        "a stack overflow question page",
        "a login page in browser",
        "a dashboard in browser",
        "a settings page in browser",
        "a web app form",
        "a browser tab with an error page",
        "a browser with multiple tabs",
        "a browser showing a chart",
    ],
    "communication": [
        "a chat application with messages",
        "a whatsapp conversation",
        "a discord server chat",
        "a slack workspace chat",
        "an email inbox",
        "a calendar meeting screen",
        "a video call with webcam feeds",
        "a messaging app with unread messages",
        "a group chat conversation",
        "a direct message chat window",
        "a notification center with messages",
        "an email compose window",
    ],
    "social_media": [
        "a social media feed with posts",
        "an instagram feed",
        "a twitter timeline",
        "a reddit thread",
        "a youtube home page",
        "a comment section on social media",
        "a short video feed",
        "a social media profile page",
        "a social media story view",
        "a social media notifications page",
    ],
    "productivity": [
        "a document editor",
        "a note-taking application",
        "a to-do list app",
        "a kanban board",
        "a project management board",
        "a spreadsheet with rows and columns",
        "a presentation slide editor",
        "a whiteboard application",
        "a file manager window",
        "a task tracker dashboard",
        "a timeline planning view",
        "a markdown document editor",
    ],
    "media": [
        "a video player screen",
        "a music player app",
        "a photo gallery view",
        "an image editor interface",
        "a streaming platform page",
        "a paused video with controls",
        "an audio playback timeline",
        "a subtitle panel on video",
        "a fullscreen media player",
        "a playlist view in media app",
    ],
    "shopping": [
        "an ecommerce product listing page",
        "a product details page",
        "an online shopping cart",
        "a checkout page",
        "a payment details form",
        "an order history page",
        "a product comparison page",
        "an online marketplace listing",
        "a food delivery app page",
        "a travel booking page",
    ],
    "finance": [
        "a finance dashboard",
        "a banking website page",
        "a payment confirmation screen",
        "an expense tracker app",
        "a stock chart page",
        "a crypto exchange interface",
        "a budget planning spreadsheet",
        "an invoice page",
        "a billing settings page",
        "a transaction history table",
    ],
    "gaming": [
        "a game launcher window",
        "a game pause menu",
        "a game settings panel",
        "a live game stream",
        "a game inventory screen",
        "a multiplayer lobby screen",
        "a game map interface",
        "a game results screen",
    ],
    "design": [
        "a figma design canvas",
        "a figjam board",
        "a UI mockup in design tool",
        "a component library in design app",
        "a color palette panel",
        "a typography settings panel",
        "a prototyping flow screen",
        "a design review board",
        "an artboard with layout guides",
        "a vector graphics editor",
    ],
    "documents": [
        "a pdf document view",
        "a research paper page",
        "a technical specification document",
        "a legal document page",
        "a meeting notes document",
        "a markdown readme file",
        "a changelog document",
        "an instruction manual page",
        "a report document with headings",
        "an article page with text",
    ],
    "system": [
        "an operating system settings window",
        "a file open dialog",
        "a save dialog box",
        "a system notification popup",
        "a software update prompt",
        "a permission dialog",
        "a network settings panel",
        "a bluetooth devices panel",
        "a battery settings screen",
        "a printer settings dialog",
    ],
    "visual_content": [
        "a photo of a person's face",
        "a group photo of multiple people",
        "a selfie photo",
        "a family photo",
        "food on a plate",
        "a pet photo",
        "a travel landscape photo",
        "a city street photo",
        "a screenshot of a meme",
        "a chart or graph visualization",
        "an infographic image",
        "a scanned handwritten note",
    ],
    "errors_warnings": [
        "an error message dialog",
        "a warning notification",
        "a failed build screen",
        "a crash report window",
        "a 404 page not found",
        "a server error page",
        "a permission denied message",
        "a connection timeout error",
        "a syntax error shown in editor",
        "a failed test report",
        "a red alert banner",
    ],
    "activities": [
        "someone actively typing text",
        "someone scrolling through content",
        "a modal dialog or popup",
        "a search bar with typed query",
        "a form being filled out",
        "a loading spinner indicator",
        "a drag and drop interaction",
        "a copy paste action",
        "a tab switching activity",
        "a file upload in progress",
        "a screen sharing session",
        "an idle screen with no changes",
    ],
}

_APP_NAMES = [
    "Visual Studio Code",
    "Cursor",
    "IntelliJ IDEA",
    "PyCharm",
    "Windows Terminal",
    "PowerShell",
    "Chrome",
    "Edge",
    "Firefox",
    "Brave",
    "Slack",
    "Discord",
    "WhatsApp",
    "Telegram",
    "Notion",
    "Obsidian",
    "Figma",
    "FigJam",
    "GitHub",
    "Linear",
    "Jira",
    "Excel",
    "Google Docs",
    "YouTube",
]

_APP_TEMPLATES = [
    "{} application interface",
    "{} window with active content",
    "{} screen during normal usage",
    "{} app with a focused task",
]


def _expand_app_prompts() -> list[str]:
    out: list[str] = []
    for app in _APP_NAMES:
        for tpl in _APP_TEMPLATES:
            out.append(tpl.format(app))
    return out


def get_seed_concepts() -> dict[str, list[str]]:
    """
    Return categorized seed concepts.
    Includes generated app-level concepts for broad cold-start coverage.
    """
    categories = {k: list(v) for k, v in _CATEGORIES.items()}
    categories["apps_generic"] = _expand_app_prompts()
    return categories


def all_seed_prompts() -> list[str]:
    """Return deduplicated seed prompts preserving insertion order."""
    seen: set[str] = set()
    prompts: list[str] = []
    for values in get_seed_concepts().values():
        for p in values:
            s = p.strip()
            if s and s not in seen:
                seen.add(s)
                prompts.append(s)
    return prompts

