import { describe, expect, it } from 'vitest';
import { greet } from './core';

describe('greet', () => {
  it('greets by name', () => {
    expect(greet('Ada')).toBe('Hello, Ada!');
  });

  it('falls back for blank input', () => {
    expect(greet('   ')).toBe('Hello, stranger!');
  });
});
