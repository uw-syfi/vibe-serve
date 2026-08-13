import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import useBaseUrl from '@docusaurus/useBaseUrl';
import Layout from '@theme/Layout';
import HomepageFeatures from '@site/src/components/HomepageFeatures';
import Heading from '@theme/Heading';

import styles from './index.module.css';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero hero--primary', styles.heroBanner)}>
      <div className="container">
        <Heading as="h1" className="hero__title">
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
            className="button button--secondary button--lg"
            to="/docs/running-vibesys">
            Read the docs
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
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
            <p className={styles.architectureCaption}>
              An outer loop plans the search over designs; an inner loop
              implements and validates candidates; an independent judge checks
              correctness before results are recorded.
            </p>
            <img
              src={useBaseUrl('/img/architecture.png')}
              alt="VibeSys architecture: an outer loop dispatches per-round tasks to an inner loop of Implementer, Accuracy Judge, and Performance Evaluator agents"
              className={styles.architectureImg}
            />
          </div>
        </section>
      </main>
    </Layout>
  );
}
