// Pure logic without DOM access, so it can be unit-tested in Node.
export function greet(name: string): string {
  const trimmed = name.trim();
  return `Hello, ${trimmed === '' ? 'stranger' : trimmed}!`;
}
