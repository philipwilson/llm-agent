---
name: webapp-testing
description: Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.
---

# Web Application Testing

To test local web applications, write native Python Playwright scripts.

**Prerequisites**: Install Playwright (`pip install playwright && playwright install chromium`).

## Decision Tree: Choosing Your Approach

```
User task -> Is it static HTML?
    +- Yes -> Read HTML file directly to identify selectors
    |         +- Success -> Write Playwright script using selectors
    |         +- Fails/Incomplete -> Treat as dynamic (below)
    |
    +- No (dynamic webapp) -> Is the server already running?
        +- No -> Start the server first, then run Playwright
        |
        +- Yes -> Reconnaissance-then-action:
            1. Navigate and wait for networkidle
            2. Take screenshot or inspect DOM
            3. Identify selectors from rendered state
            4. Execute actions with discovered selectors
```

## Starting a Server for Testing

If the server isn't already running, start it in the background before running your Playwright script:

**Single server:**
```bash
# Start server in background
npm run dev &
SERVER_PID=$!

# Wait for it to be ready
sleep 3  # or use a more robust check:
# while ! curl -s http://localhost:5173 > /dev/null; do sleep 1; done

# Run your test
python your_automation.py

# Clean up
kill $SERVER_PID
```

**Multiple servers (e.g., backend + frontend):**
```bash
# Start backend
cd backend && python server.py &
BACKEND_PID=$!

# Start frontend
cd frontend && npm run dev &
FRONTEND_PID=$!

# Wait for both
sleep 5

# Run tests
python your_automation.py

# Clean up
kill $FRONTEND_PID $BACKEND_PID
```

**Or use a Python wrapper for better control:**
```python
import subprocess, time, signal

# Start server
server = subprocess.Popen(['npm', 'run', 'dev'], cwd='./frontend')
time.sleep(3)  # Wait for startup

try:
    # Your Playwright code here
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto('http://localhost:5173')
        page.wait_for_load_state('networkidle')
        # ... automation logic
        browser.close()
finally:
    server.send_signal(signal.SIGTERM)
    server.wait()
```

## Writing Playwright Scripts

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)  # Always launch chromium in headless mode
    page = browser.new_page()
    page.goto('http://localhost:5173')
    page.wait_for_load_state('networkidle')  # CRITICAL: Wait for JS to execute
    # ... your automation logic
    browser.close()
```

## Reconnaissance-Then-Action Pattern

1. **Inspect rendered DOM**:
   ```python
   page.screenshot(path='/tmp/inspect.png', full_page=True)
   content = page.content()
   page.locator('button').all()
   ```

2. **Identify selectors** from inspection results

3. **Execute actions** using discovered selectors

## Common Pitfall

- **Don't** inspect the DOM before waiting for `networkidle` on dynamic apps
- **Do** wait for `page.wait_for_load_state('networkidle')` before inspection

## Best Practices

- Use `sync_playwright()` for synchronous scripts
- Always close the browser when done
- Use descriptive selectors: `text=`, `role=`, CSS selectors, or IDs
- Add appropriate waits: `page.wait_for_selector()` or `page.wait_for_timeout()`
- Capture screenshots for debugging: `page.screenshot(path='/tmp/debug.png')`
- Capture console logs:
  ```python
  page.on('console', lambda msg: print(f'Console {msg.type}: {msg.text}'))
  ```

## Common Patterns

### Element Discovery
```python
# Find all buttons
buttons = page.locator('button').all()
for btn in buttons:
    print(f"Button: {btn.text_content()}")

# Find all links
links = page.locator('a').all()
for link in links:
    print(f"Link: {link.get_attribute('href')} - {link.text_content()}")

# Find all inputs
inputs = page.locator('input').all()
for inp in inputs:
    print(f"Input: {inp.get_attribute('name')} type={inp.get_attribute('type')}")
```

### Form Interaction
```python
page.fill('input[name="email"]', 'test@example.com')
page.fill('input[name="password"]', 'password123')
page.click('button[type="submit"]')
page.wait_for_load_state('networkidle')
```

### Static HTML Testing
```python
# For local HTML files, use file:// URLs
page.goto(f'file:///absolute/path/to/index.html')
```