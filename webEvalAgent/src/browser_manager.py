#!/usr/bin/env python3

import asyncio
import base64
import socket
from typing import Dict, List, Optional
import logging

# Import log server functions
# We will add send_browser_view later
from .log_server import start_log_server, open_log_dashboard, send_log, send_browser_view

class PlaywrightBrowserManager:
    # Class variable to hold the singleton instance
    _instance: Optional['PlaywrightBrowserManager'] = None
    _log_server_started = False # Flag to ensure server starts only once
    
    @classmethod
    def get_instance(cls) -> 'PlaywrightBrowserManager':
        """Get or create the singleton instance of PlaywrightBrowserManager."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        # Check if an instance already exists
        if PlaywrightBrowserManager._instance is not None:
            logging.warning("PlaywrightBrowserManager is a singleton. Use get_instance() instead.")
            return
            
        # Set this instance as the singleton
        PlaywrightBrowserManager._instance = self
        
        self.playwright = None
        self.browser = None
        self.page = None
        self.cdp_session = None # Added for CDP
        self.screencast_task_running = False # Added for screencast state
        self.console_logs = []
        self.network_requests = []
        self.is_initialized = False

    async def initialize(self) -> None:
        """Initialize the Playwright browser if not already initialized."""
        if self.is_initialized:
            return
            
        if not PlaywrightBrowserManager._log_server_started:
            try:
                # Send status message
                send_log("Initializing Operative Agent (Browser Manager)...", "🚀", log_type='status')
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    s.connect(('localhost', 5009))
                    s.close()
                    print("Log server already appears to be running, skipping initialization")
                    PlaywrightBrowserManager._log_server_started = True
                    # Send status message
                    send_log("Connected to existing log server (Browser Manager).", "✅", log_type='status')
                except (socket.error, Exception):
                    s.close()
                    start_log_server() # This itself sends logs now
                    await asyncio.sleep(1)
                    open_log_dashboard() # This itself sends logs now
                    PlaywrightBrowserManager._log_server_started = True
                    # Send status message - might be redundant if start_log_server logs
                    # send_log("Log server started and dashboard opened from browser manager.", "✅", log_type='status')
            except Exception as e:
                print(f"Error starting/checking log server/dashboard: {e}")
                # Send status message
                send_log(f"Error with log server/dashboard (Browser Manager): {e}", "❌", log_type='status')

        # Import here to avoid module import issues
        # import asyncio  # Already imported at the top of the file
        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()
        # Launch headless
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.is_initialized = True
        # Send status message
        send_log("Playwright initialized (Browser Manager - Headless).", "🎭", log_type='status')

    async def close(self) -> None:
        """Close the browser and Playwright instance."""
        # Stop screencast if running
        if self.cdp_session and self.screencast_task_running:
            try:
                await self.cdp_session.send("Page.stopScreencast")
            except Exception as e:
                logging.error(f"Error stopping screencast: {e}")
            self.screencast_task_running = False

        # Detach CDP session if exists
        if self.cdp_session:
            try:
                await self.cdp_session.detach()
            except Exception as e:
                logging.error(f"Error detaching CDP session: {e}")
            self.cdp_session = None

        if self.page:
            try:
                await self.page.close()
            except Exception as e:
                 logging.error(f"Error closing page: {e}")
            self.page = None

        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                 logging.error(f"Error closing browser: {e}")
            self.browser = None
            
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

        self.is_initialized = False
        self.console_logs = []
        self.network_requests = []
        # Send status message
        send_log("Browser manager closed.", "🛑", log_type='status')

    async def open_url(self, url: str) -> str:
        """Open a URL in the browser and start monitoring console and network.
        The browser will stay open for user interaction."""
        if not self.is_initialized:
            await self.initialize()

        # Stop screencast and close previous page/session if they exist
        if self.cdp_session and self.screencast_task_running:
            try:
                await self.cdp_session.send("Page.stopScreencast")
            except Exception as e:
                logging.warning(f"Could not stop previous screencast: {e}")
            self.screencast_task_running = False
        if self.cdp_session:
             try:
                 await self.cdp_session.detach()
             except Exception as e:
                 logging.warning(f"Could not detach previous CDP session: {e}")
             self.cdp_session = None
        if self.page:
            try:
                await self.page.close()
            except Exception as e:
                logging.warning(f"Could not close previous page: {e}")
            self.page = None

        # Clear previous logs and requests
        self.console_logs = []
        self.network_requests = []
        
        # Create a new page
        self.page = await self.browser.new_page()
        
        # Set up console log listener
        self.page.on("console", lambda message: asyncio.create_task(self._handle_console_message(message)))
        
        # Set up network request listener
        self.page.on("request", lambda request: asyncio.create_task(self._handle_request(request)))
        self.page.on("response", lambda response: asyncio.create_task(self._handle_response(response)))
        
        # Navigate to the URL
        await self.page.goto(url, wait_until="networkidle")
        # Send agent/status message
        send_log(f"Navigated to: {url} (Headless Mode)", "🌍", log_type='agent')

        # --- Start CDP Screencast ---
        try:
            self.cdp_session = await self.page.context.new_cdp_session(self.page)
            # Listen for screencast frames
            self.cdp_session.on("Page.screencastFrame", self._handle_screencast_frame)
            logging.debug("Attempting to start CDP screencast...")
            # Start the screencast
            await self.cdp_session.send("Page.startScreencast", {
                "format": "png",  # jpeg is generally smaller than png
                "quality": 100,     # Adjust quality vs size (0-100)
                "maxWidth": 1920,  # Optional: limit width
                "maxHeight": 1080   # Optional: limit height
            })
            self.screencast_task_running = True
            logging.info("CDP screencast started successfully.")
            send_log("CDP screencast started.", "📹", log_type='status')
        except Exception as e:
            logging.exception(f"Failed to start CDP screencast: {e}") # Use logging.exception to include traceback
            send_log(f"Failed to start CDP screencast: {e}", "❌", log_type='status')
            self.screencast_task_running = False
            if self.cdp_session: # Attempt cleanup if session was created
                 try: await self.cdp_session.detach()
                 except: pass
                 self.cdp_session = None
            # Return an error message or raise? For now, log and continue
            return f"Opened {url}, but failed to start screen streaming."

        return f"Opened {url} successfully in headless mode. Streaming view to dashboard."

    async def _handle_console_message(self, message) -> None:
        """Handle console messages from the page."""
        log_entry = {
            "type": message.type,
            "text": message.text,
            "location": message.location,
            "timestamp": asyncio.get_event_loop().time()
        }
        self.console_logs.append(log_entry)
        # Send console log to dashboard with type 'console'
        # Use try-except as send_log might fail if server isn't ready
        try:
            send_log(f"CONSOLE [{log_entry['type']}]: {log_entry['text']}", "🖥️", log_type='console')
        except Exception as e:
            logging.warning(f"Failed to send console log via SocketIO: {e}")

    async def _handle_request(self, request) -> None:
        """Handle network requests."""
        request_entry = {
            "url": request.url,
            "method": request.method,
            "headers": request.headers,
            "timestamp": asyncio.get_event_loop().time(),
            "resourceType": request.resource_type,
            "id": id(request)
        }
        self.network_requests.append(request_entry)
        # Send request info to dashboard with type 'network'
        try:
            send_log(f"NET REQ [{request_entry['method']}]: {request_entry['url']}", "➡️", log_type='network')
        except Exception as e:
            logging.warning(f"Failed to send network request log via SocketIO: {e}")

    async def _handle_response(self, response) -> None:
        """Handle network responses."""
        response_timestamp = asyncio.get_event_loop().time()
        response_data = {
            "status": response.status,
            "statusText": response.status_text,
            "headers": response.headers,
            "timestamp": response_timestamp
        }
        # Find the matching request and update it with response data
        found = False
        for req in self.network_requests:
            # Use id for more reliable matching if available
            if req.get("id") == id(response.request) and "response" not in req:
                req["response"] = response_data
                # Send response info to dashboard with type 'network'
                try:
                    send_log(f"NET RESP [{response_data['status']}]: {req['url']}", "⬅️", log_type='network')
                except Exception as e:
                    logging.warning(f"Failed to send network response log via SocketIO: {e}")
                found = True
                break
        if not found:
             # Log responses even if request wasn't found with type 'network'
             try:
                 send_log(f"NET RESP* [{response_data['status']}]: {response.url} (request not matched)", "⬅️", log_type='network')
             except Exception as e:
                 logging.warning(f"Failed to send unmatched network response log via SocketIO: {e}")

    async def get_console_logs(self, last_n: int) -> List[Dict]:
        """Get console logs collected so far with deduplication of repeated messages."""
        if not self.console_logs:
            return []
            
        # Create a deduplicated version of the logs
        deduplicated_logs = []
        current_group = None
        
        # Sort logs by timestamp to ensure proper grouping
        sorted_logs = sorted(self.console_logs, key=lambda x: x.get('timestamp', 0))
        
        for log in sorted_logs:
            # If we have no current group or this log is different from the current group
            if (current_group is None or 
                log['type'] != current_group['type'] or 
                log['text'] != current_group['text']):
                
                # Start a new group
                current_group = {
                    'type': log['type'],
                    'text': log['text'],
                    'location': log['location'],
                    'timestamp': log['timestamp'],
                    'count': 1,
                    'timestamps': [log['timestamp']]
                }
                deduplicated_logs.append(current_group)
            else:
                # This is a repeated message, increment count and add timestamp
                current_group['count'] += 1
                current_group['timestamps'].append(log['timestamp'])
                # Update the text to show repetition count for repeated messages
                if current_group['count'] > 1:
                    current_group['text'] = f"{log['text']} (repeated {current_group['count']} times)"
        
        # Return only the last N entries
        # Sort by timestamp (descending) to get the most recent logs first
        deduplicated_logs.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        deduplicated_logs = deduplicated_logs[:last_n]
        # Sort back by timestamp (ascending) for consistent output
        deduplicated_logs.sort(key=lambda x: x.get('timestamp', 0))
        
        return deduplicated_logs

    async def get_network_requests(self, last_n: int) -> List[Dict]:
        """Get network requests collected so far."""
        if not self.network_requests:
            return []
            
        # Sort by timestamp (descending) to get the most recent requests first
        sorted_requests = sorted(self.network_requests, key=lambda x: x.get('timestamp', 0), reverse=True)
        # Take only the last N entries
        limited_requests = sorted_requests[:last_n]
        # Sort back by timestamp (ascending) for consistent output
        limited_requests.sort(key=lambda x: x.get('timestamp', 0))
        
        return limited_requests
        
    def get_browser(self):
        """Get the browser instance to pass to the Agent."""
        if not self.is_initialized:
            raise RuntimeError("Browser manager not initialized. Call initialize() first.")
        return self.browser

    # --- CDP Screencast Handling ---
    async def _handle_screencast_frame(self, params: Dict) -> None:
        """Handle incoming screencast frames from CDP."""
        if not self.cdp_session:
            return # Session closed or not initialized

        image_data = params.get('data')
        session_id = params.get('sessionId')

        if image_data and session_id:
            logging.debug(f"Received screencast frame (sessionId: {session_id}), data length: {len(image_data)}")
            # Format as data URL
            image_data_url = f"data:image/jpeg;base64,{image_data}"

            # Send to frontend via SocketIO
            try:
                logging.debug(f"Calling send_browser_view for frame (sessionId: {session_id})...")
                # Use asyncio.create_task to avoid blocking the CDP event handler
                asyncio.create_task(send_browser_view(image_data_url))
                logging.debug(f"Scheduled send_browser_view for frame (sessionId: {session_id}).")
            except Exception as e:
                logging.error(f"Failed to send browser view update: {e}")

            # IMPORTANT: Acknowledge the frame back to the browser
            try:
                logging.debug(f"Acknowledging screencast frame (sessionId: {session_id})...")
                await self.cdp_session.send("Page.screencastFrameAck", {"sessionId": session_id})
                logging.debug(f"Acknowledged screencast frame (sessionId: {session_id}).")
            except Exception as e:
                # If acknowledging fails, the stream might stop. Log error.
                logging.error(f"Failed to acknowledge screencast frame (sessionId: {session_id}): {e}")
                # Consider stopping the screencast or attempting to recover?
                # For now, just log the error. If the session is closed, this will likely fail.
                if "Target closed" in str(e) or "Session closed" in str(e) or "Connection closed" in str(e):
                    logging.warning(f"CDP session seems closed while acknowledging frame (sessionId: {session_id}), stopping screencast handling.")
                    self.screencast_task_running = False # Mark as stopped
                    if self.cdp_session:
                        try: await self.cdp_session.detach()
                        except: pass
                        self.cdp_session = None


    # --- Input Handling ---
    async def handle_browser_input(self, event_type: str, details: Dict) -> None:
        """Handles input events received from the frontend via log_server."""
        if not self.cdp_session or not self.screencast_task_running:
            logging.warning(f"Cannot handle browser input '{event_type}': No active CDP session or screencast stopped.")
            return

        logging.debug(f"Processing browser input event: {event_type}, Details: {details}")

        try:
            if event_type == 'click':
                # CDP expects separate press and release events for a click
                button = details.get('button', 'left')
                x = details.get('x', 0)
                y = details.get('y', 0)
                click_count = details.get('clickCount', 1)
                # Modifiers might be needed for complex interactions, but start simple
                modifiers = 0 # TODO: Map ctrlKey, shiftKey etc. if needed

                # Mouse Pressed
                await self.cdp_session.send("Input.dispatchMouseEvent", {
                    "type": "mousePressed",
                    "button": button,
                    "x": x,
                    "y": y,
                    "modifiers": modifiers,
                    "clickCount": click_count
                })
                # Short delay often helps reliability
                await asyncio.sleep(0.05)
                # Mouse Released
                await self.cdp_session.send("Input.dispatchMouseEvent", {
                    "type": "mouseReleased",
                    "button": button,
                    "x": x,
                    "y": y,
                    "modifiers": modifiers,
                    "clickCount": click_count
                })
                logging.debug(f"Sent CDP click event at ({x},{y}), button: {button}")

            elif event_type == 'keydown':
                # Map frontend details to CDP key event parameters
                # Note: CDP parameters like 'text', 'unmodifiedText', 'keyIdentifier' can be complex.
                # Using 'key' and 'code' is often sufficient for basic typing.
                # 'windowsVirtualKeyCode' might be needed for some keys.
                await self.cdp_session.send("Input.dispatchKeyEvent", {
                    "type": "keyDown",
                    "modifiers": self._map_modifiers(details),
                    "key": details.get('key', ''),
                    "code": details.get('code', ''),
                    # "text": details.get('key', ''), # Simplistic mapping for printable chars
                    # "unmodifiedText": details.get('key', ''), # Simplistic
                })
                logging.debug(f"Sent CDP keydown event: key={details.get('key')}")

            elif event_type == 'keyup':
                await self.cdp_session.send("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                     "modifiers": self._map_modifiers(details),
                     "key": details.get('key', ''),
                     "code": details.get('code', ''),
                })
                logging.debug(f"Sent CDP keyup event: key={details.get('key')}")

            elif event_type == 'scroll':
                # Use dispatchMouseEvent with type 'mouseWheel'
                x = details.get('x', 0)
                y = details.get('y', 0)
                delta_x = details.get('deltaX', 0)
                delta_y = details.get('deltaY', 0)
                await self.cdp_session.send("Input.dispatchMouseEvent", {
                    "type": "mouseWheel",
                    "x": x,
                    "y": y,
                    "deltaX": delta_x,
                    "deltaY": delta_y,
                    "modifiers": 0 # Modifiers usually not needed for scroll
                })
                logging.debug(f"Sent CDP scroll event: dX={delta_x}, dY={delta_y} at ({x},{y})")

            else:
                logging.warning(f"Received unknown browser input event type: {event_type}")

        except Exception as e:
            logging.error(f"Error dispatching CDP input event '{event_type}': {e}")
            # Check if the session is closed
            if "Target closed" in str(e) or "Session closed" in str(e):
                 logging.warning("CDP session seems closed, stopping input handling.")
                 self.screencast_task_running = False # Mark as stopped
                 if self.cdp_session:
                     try: await self.cdp_session.detach()
                     except: pass
                     self.cdp_session = None

    def _map_modifiers(self, details: Dict) -> int:
        """Maps modifier keys from frontend details to CDP modifier bitmask."""
        modifiers = 0
        if details.get('altKey'): modifiers |= 1
        if details.get('ctrlKey'): modifiers |= 2
        if details.get('metaKey'): modifiers |= 4 # Command key on Mac
        if details.get('shiftKey'): modifiers |= 8
        return modifiers
