import { defineConfig } from 'vitest/config';

export default defineConfig({
  build: {
    sourcemap: true,
  },
  test: {
    include: ['src/**/*.test.ts'],
    // Use 'jsdom' or 'happy-dom' (extra devDependency) once tests touch the DOM.
    environment: 'node',
  },
});
