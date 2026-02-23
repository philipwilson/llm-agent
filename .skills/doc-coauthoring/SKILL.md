---
name: doc-coauthoring
description: Guide users through a structured workflow for co-authoring documentation. Use when user wants to write documentation, proposals, technical specs, decision docs, or similar structured content. This workflow helps users efficiently transfer context, refine content through iteration, and verify the doc works for readers. Trigger when user mentions writing docs, creating proposals, drafting specs, or similar documentation tasks.
---

# Doc Co-Authoring Workflow

This skill provides a structured workflow for guiding users through collaborative document creation. Act as an active guide, walking users through three stages: Context Gathering, Refinement & Structure, and Reader Testing.

## When to Offer This Workflow

**Trigger conditions:**
- User mentions writing documentation: "write a doc", "draft a proposal", "create a spec", "write up"
- User mentions specific doc types: "PRD", "design doc", "decision doc", "RFC"
- User seems to be starting a substantial writing task

**Initial offer:**
Offer the user a structured workflow for co-authoring the document. Explain the three stages:

1. **Context Gathering**: User provides all relevant context while you ask clarifying questions
2. **Refinement & Structure**: Iteratively build each section through brainstorming and editing
3. **Reader Testing**: Test the doc with a fresh subagent (no context) to catch blind spots

Ask if they want to try this workflow or prefer to work freeform.

## Stage 1: Context Gathering

**Goal:** Close the gap between what the user knows and what you know, enabling smart guidance later.

### Initial Questions

Start by asking the user for meta-context about the document:

1. What type of document is this? (e.g., technical spec, decision doc, proposal)
2. Who's the primary audience?
3. What's the desired impact when someone reads this?
4. Is there a template or specific format to follow?
5. Any other constraints or context to know?

Inform them they can answer in shorthand or dump information however works best for them.

**If user provides a template or mentions a doc type:**
- Ask if they have a template document to share
- If they provide a file, read it

**If user mentions editing an existing document:**
- Read the current state of the file
- Check for images without alt-text and offer to help describe them

### Info Dumping

Once initial questions are answered, encourage the user to dump all the context they have:
- Background on the project/problem
- Related discussions or documents
- Why alternative solutions aren't being used
- Organizational context
- Timeline pressures or constraints
- Technical architecture or dependencies
- Stakeholder concerns

Advise them not to worry about organizing it - just get it all out.

**Asking clarifying questions:**

When user signals they've done their initial dump, ask 5-10 numbered clarifying questions based on gaps in the context. Inform them they can use shorthand to answer.

**Exit condition:**
Sufficient context has been gathered when you can ask about edge cases and trade-offs without needing basics explained.

**Transition:**
Ask if there's any more context they want to provide, or if it's time to move on to drafting.

## Stage 2: Refinement & Structure

**Goal:** Build the document section by section through brainstorming, curation, and iterative refinement.

**Instructions to user:**
Explain that the document will be built section by section. For each section:
1. Clarifying questions will be asked about what to include
2. 5-20 options will be brainstormed
3. User will indicate what to keep/remove/combine
4. The section will be drafted
5. It will be refined through surgical edits

Start with whichever section has the most unknowns.

**Once structure is agreed:**

Create the initial document file with placeholder text for all sections. Name it appropriately (e.g., `decision-doc.md`, `technical-spec.md`).

**For each section:**

### Step 1: Clarifying Questions
Ask 5-10 specific questions about what should be included.

### Step 2: Brainstorming
Brainstorm 5-20 things that might be included. Look for context the user shared that might have been forgotten, and angles not yet mentioned.

### Step 3: Curation
Ask which points to keep, remove, or combine. Request brief justifications.

### Step 4: Gap Check
Ask if there's anything important missing.

### Step 5: Drafting
Write the section content into the document file using the edit_file tool.

**Key instruction for user (include when drafting the first section):**
Instead of editing the doc directly, ask them to indicate what to change. This helps you learn their style for future sections.

### Step 6: Iterative Refinement
As user provides feedback, make surgical edits to the file. Continue iterating until user is satisfied with the section.

### Quality Checking
After 3 consecutive iterations with no substantial changes, ask if anything can be removed without losing important information.

### Near Completion
When 80%+ of sections are done, re-read the entire document and check for:
- Flow and consistency across sections
- Redundancy or contradictions
- Generic filler content
- Whether every sentence carries weight

## Stage 3: Reader Testing

**Goal:** Test the document with a fresh perspective to verify it works for readers.

### Testing with Subagent

Use the `delegate` tool with the `explore` agent to test the document:

### Step 1: Predict Reader Questions
Generate 5-10 questions that readers would realistically ask.

### Step 2: Test with Subagent
For each question, delegate to a subagent with just the document content and the question. The subagent has no context from this conversation, giving it fresh eyes.

### Step 3: Run Additional Checks
Delegate a subagent to check for ambiguity, false assumptions, and contradictions.

### Step 4: Report and Fix
If issues found, report them and loop back to refinement for problematic sections.

### Exit Condition
When the subagent consistently answers questions correctly and doesn't surface new gaps, the doc is ready.

## Final Review

When Reader Testing passes:
1. Recommend they do a final read-through themselves
2. Suggest double-checking any facts, links, or technical details
3. Ask them to verify it achieves the impact they wanted

## Tips for Effective Guidance

**Tone:**
- Be direct and procedural
- Don't try to "sell" the approach - just execute it

**Handling Deviations:**
- If user wants to skip a stage: let them
- Always give user agency to adjust the process

**Quality over Speed:**
- Don't rush through stages
- Each iteration should make meaningful improvements
- The goal is a document that actually works for readers