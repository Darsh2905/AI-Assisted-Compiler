// Small numeric kernels: primality, integer power, popcount, prefix sums.
bool is_prime(int n) {
  if (n < 2) {
    return false;
  }
  int d = 2;
  while (d * d <= n) {
    if (n % d == 0) {
      return false;
    }
    d = d + 1;
  }
  return true;
}

int ipow(int base, int e) {
  int acc = 1;
  while (e > 0) {
    if (e % 2 == 1) {
      acc = acc * base;
    }
    base = base * base;
    e = e / 2;
  }
  return acc;
}

int popcount(int x) {
  int n = 0;
  for (int i = 0; i < 32; i = i + 1) {
    n = n + ((x >> i) & 1);
  }
  return n;
}

int prefix_sum(int xs[10]) {
  int total = 0;
  for (int i = 0; i < 10; i = i + 1) {
    total = total + xs[i];
    xs[i] = total;
  }
  return total;
}

int main() {
  int xs[10];
  for (int i = 0; i < 10; i = i + 1) {
    xs[i] = i * 2;
  }
  int s = prefix_sum(xs);
  if (is_prime(97)) {
    return s + ipow(2, 5) + popcount(255);
  }
  return s;
}
