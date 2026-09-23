// Euclid's algorithm. Exercises srem, loops, and signed comparison.
int gcd(int a, int b) {
  while (b != 0) {
    int t = b;
    b = a % b;
    a = t;
  }
  if (a < 0) {
    return 0 - a;
  }
  return a;
}

int lcm(int a, int b) {
  int g = gcd(a, b);
  if (g == 0) {
    return 0;
  }
  return a / g * b;
}

int main() {
  return gcd(1071, 462) + lcm(4, 6);
}
