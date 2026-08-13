import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)
//
// Doc ids are the repo docs/ filenames (the docs plugin `path` points at
// ../docs), so the site renders a single copy of those Markdown files.
const sidebars: SidebarsConfig = {
  docsSidebar: [
    'running-vibesys',
    'development',
    'cli-flags',
    'coding-best-practices',
    'extending-profilers',
    'openevolve',
    'publishing',
    'skill-metadata',
    'issue-authoring',
  ],
};

export default sidebars;
