import {defineConfig} from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    // Avoid forking Bun workers; spawned Bun processes fail with EACCES in CI.
    pool: 'threads',
  },
});
