import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

const config: Config = {
  title: 'VibeSys',
  tagline: 'Generating Bespoke Systems with AI Agents',
  favicon: 'img/favicon.ico',

  future: {
    v4: true, // Improve compatibility with the upcoming Docusaurus v4
  },

  // Production URL. GitHub Pages for the uw-syfi/vibesys repo.
  url: 'https://uw-syfi.github.io',
  baseUrl: '/vibesys/',

  organizationName: 'uw-syfi',
  projectName: 'vibesys',

  onBrokenLinks: 'warn',
  onBrokenMarkdownLinks: 'warn',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/uw-syfi/vibesys/tree/main/website/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/vibesys-social-card.jpg',
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'VibeSys',
      logo: {
        alt: 'VibeSys Logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://arxiv.org/abs/2605.06068',
          label: 'Paper',
          position: 'right',
        },
        {
          href: 'https://github.com/uw-syfi/vibesys',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Introduction', to: '/docs/intro'},
            {label: 'Development', to: '/docs/development'},
            {label: 'CLI flags', to: '/docs/cli-flags'},
          ],
        },
        {
          title: 'Project',
          items: [
            {
              label: 'VibeServe blog post',
              href: 'https://syfi.cs.washington.edu/blog/2026-05-12-introducing-vibeserve/',
            },
            {label: 'Paper (arXiv)', href: 'https://arxiv.org/abs/2605.06068'},
            {label: 'SyFI Lab', href: 'https://syfi.cs.washington.edu/'},
          ],
        },
        {
          title: 'More',
          items: [
            {label: 'GitHub', href: 'https://github.com/uw-syfi/vibesys'},
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} University of Washington SyFI Lab. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
