import {useState, type ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import HomepageFeatures from '@site/src/components/HomepageFeatures';
import ArchitectureDiagram from '@site/src/components/ArchitectureDiagram';
import Heading from '@theme/Heading';

import styles from './index.module.css';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero', styles.heroBanner)}>
      <div className="container">
        <Heading as="h1" className="hero__title">
          <span className={styles.promptMark}>&gt;</span>
          {siteConfig.title}
        </Heading>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <p className={styles.heroDescription}>
          VibeSys uses application requirements, workload characteristics, and
          hardware capabilities as inputs to an agentic search that generates a
          purpose-built system for each target.
        </p>
        <div className={styles.buttons}>
          <Link
            className="button button--primary button--lg"
            to="/docs/running-vibesys">
            Read the docs
          </Link>
          <Link
            className="button button--outline button--primary button--lg"
            href="https://arxiv.org/abs/2605.06068">
            Paper (arXiv)
          </Link>
        </div>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  const [activeId, setActiveId] = useState<string | null>(null);
  return (
    <Layout
      title={siteConfig.title}
      description="Generating bespoke systems with AI agents.">
      <HomepageHeader />
      <main>
        <HomepageFeatures />
        <section className={styles.architecture}>
          <div className="container text--center">
            <Heading as="h2">Architecture</Heading>
            <p className={styles.architectureNote}>
              Click a use case on the left to see its input, system, and result on the right.
            </p>
            <ArchitectureDiagram activeId={activeId} onSelect={setActiveId} />
          </div>
        </section>
      </main>
    </Layout>
  );
}
