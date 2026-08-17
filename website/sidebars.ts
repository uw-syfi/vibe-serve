import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)
//
// Doc ids are the repo docs/ filenames (the docs plugin `path` points at
// ../docs), so the site renders a single copy of those Markdown files.
const sidebars: SidebarsConfig = {
  docsSidebar: [
    'running-vibesys',
    'cli-flags',
    {
      type: 'category',
      label: 'Contributing',
      link: {type: 'doc', id: 'development'},
      items: [
        'development',
        'coding-best-practices',
        'extending-profilers',
        'skill-metadata',
        'openevolve',
        'issue-authoring',
        'publishing',
      ],
    },
  ],
};

export default sidebars;
