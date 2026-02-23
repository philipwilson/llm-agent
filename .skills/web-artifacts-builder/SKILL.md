---
name: web-artifacts-builder
description: Suite of tools for creating elaborate, multi-component web applications using modern frontend web technologies (React, Tailwind CSS, shadcn/ui). Use for complex web apps requiring state management, routing, or shadcn/ui components - not for simple single-file HTML/JSX pages.
---

# Web Artifacts Builder

Build powerful frontend web applications with these steps:
1. Initialize a React project
2. Develop the application
3. Bundle into a single HTML file (optional)

**Stack**: React 18 + TypeScript + Vite + Tailwind CSS + shadcn/ui

## Design & Style Guidelines

VERY IMPORTANT: To avoid what is often referred to as "AI slop", avoid using excessive centered layouts, purple gradients, uniform rounded corners, and Inter font.

## Quick Start

### Step 1: Initialize Project

Create a new React + TypeScript + Vite project:
```bash
npm create vite@latest <project-name> -- --template react-ts
cd <project-name>
npm install
```

Add Tailwind CSS:
```bash
npm install -D tailwindcss @tailwindcss/vite
```

Add to `vite.config.ts`:
```typescript
import tailwindcss from '@tailwindcss/vite'
export default defineConfig({
  plugins: [react(), tailwindcss()],
})
```

Add to your main CSS file:
```css
@import "tailwindcss";
```

Add shadcn/ui (optional, for component library):
```bash
npx shadcn@latest init
# Then add components as needed:
npx shadcn@latest add button card dialog table
```

Configure path aliases in `tsconfig.json`:
```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  }
}
```

### Step 2: Develop Your Application

Edit files in `src/` to build the application. Key files:
- `src/App.tsx` - Main application component
- `src/main.tsx` - Entry point
- `src/index.css` - Global styles

**Development server:**
```bash
npm run dev
# Opens at http://localhost:5173
```

### Step 3: Build for Production

```bash
npm run build
# Output in dist/
```

### Step 4: Bundle to Single HTML (Optional)

To create a single self-contained HTML file with all JS/CSS inlined:

```bash
npm install -D parcel html-inline @parcel/config-default
```

Create `.parcelrc`:
```json
{
  "extends": "@parcel/config-default"
}
```

Build and inline:
```bash
npx parcel build index.html --no-source-maps --dist-dir parcel-dist
npx html-inline -i parcel-dist/index.html -o bundle.html -b parcel-dist
```

### Step 5: Testing/Visualizing (Optional)

To test the application, use Playwright (see the `webapp-testing` skill) or open in a browser directly.

## Reference

- **shadcn/ui components**: https://ui.shadcn.com/docs/components
- **Tailwind CSS**: https://tailwindcss.com/docs
- **Vite**: https://vite.dev/guide/
- **React**: https://react.dev/