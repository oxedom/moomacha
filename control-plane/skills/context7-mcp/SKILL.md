---
name: context7-mcp
description: Use when the agent needs current library or framework documentation. Activates for setup questions, code generation involving libraries, or mentions of specific frameworks like React, Next.js, Prisma, etc.
---

# Context7 MCP

When asked about libraries, frameworks, or SDK APIs, use Context7 to fetch current documentation instead of relying on training data.

## When to Use This Skill

Activate when:

- The user asks a setup or configuration question ("How do I configure Next.js middleware?")
- The user requests code involving a library ("Write a Prisma query for...")
- The user needs an API reference ("What are the Supabase auth methods?")
- The user mentions a specific framework (React, Vue, Svelte, Tailwind, Prisma, Supabase, etc.)

## How to Fetch Documentation

### Step 1: Resolve the Library ID

Call `context7_resolve_library` with:

- `library_name`: the library name from the user's question
- `query`: the user's full question (improves relevance ranking)

### Step 2: Select the Best Match

From the resolution results, choose:

- The exact or closest name match
- Higher benchmark scores = better documentation quality
- If the user mentioned a version (e.g. "React 19"), prefer version-specific IDs

### Step 3: Fetch the Documentation

Call `context7_query_docs` with:

- `library_id`: the Context7 library ID from step 2 (e.g. `/vercel/next.js`)
- `query`: the user's specific question
- `tokens`: leave at default (5000) unless the user asked for a comprehensive overview

### Step 4: Answer Using the Docs

- Answer using current, accurate documentation
- Include relevant code examples
- Cite the library version when relevant

## Guidelines

- Pass the user's full question as `query` for better relevance
- When users mention versions, use version-specific library IDs if available
- Prefer official/primary packages over community forks when multiple matches exist
