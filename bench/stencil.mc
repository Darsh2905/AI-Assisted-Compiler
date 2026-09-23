// 1-D three-point stencil and a reduction, in the style of the PolyBench
// kernels the plan names as the benchmark target.
void stencil(int src[32], int dst[32]) {
  for (int i = 1; i < 31; i = i + 1) {
    dst[i] = (src[i - 1] + src[i] * 2 + src[i + 1]) / 4;
  }
  dst[0] = src[0];
  dst[31] = src[31];
}

int reduce_max(int xs[32]) {
  int best = xs[0];
  for (int i = 1; i < 32; i = i + 1) {
    if (xs[i] > best) {
      best = xs[i];
    }
  }
  return best;
}

int saxpy_like(int xs[32], int a) {
  int acc = 0;
  for (int i = 0; i < 32; i = i + 1) {
    acc = acc + a * xs[i] + xs[i];
  }
  return acc;
}

int main() {
  int src[32];
  int dst[32];
  for (int i = 0; i < 32; i = i + 1) {
    src[i] = i * i % 97;
  }
  stencil(src, dst);
  return reduce_max(dst) + saxpy_like(src, 4);
}
