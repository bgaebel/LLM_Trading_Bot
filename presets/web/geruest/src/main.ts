import { greet } from './lib/core';

const root = document.querySelector<HTMLDivElement>('#app');
if (root) {
  root.textContent = greet('world');
}
