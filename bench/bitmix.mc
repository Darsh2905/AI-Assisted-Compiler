// Bit-twiddling kernel. Written to contain the shapes the rule library
// matches: multiplication by powers of two, x + x, double negation, and
// de Morgan pairs.
int mix(int x) {
  int a = x * 8;
  int b = a + a;
  int c = b * 4;
  int d = c ^ (c >> 7);
  int e = d * 16;
  return e;
}

int fold(int x, int y) {
  int p = x & y;
  int q = x | y;
  int r = ~(x & y);
  return p + q + r;
}

int negate_twice(int x) {
  int a = 0 - x;
  int b = 0 - a;
  return b;
}

int scale(int x) {
  return x * 3 + x * 2 + x * 1;
}

int main() {
  return mix(7) + fold(12, 10) + negate_twice(5) + scale(9);
}
