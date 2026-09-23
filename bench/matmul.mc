// 4x4 integer matrix multiply over flattened arrays. Exercises nested loops,
// checked array access, and multiplication by a power-of-two stride.
void matmul(int a[16], int b[16], int c[16]) {
  for (int i = 0; i < 4; i = i + 1) {
    for (int j = 0; j < 4; j = j + 1) {
      int acc = 0;
      for (int k = 0; k < 4; k = k + 1) {
        acc = acc + a[i * 4 + k] * b[k * 4 + j];
      }
      c[i * 4 + j] = acc;
    }
  }
}

int trace(int m[16]) {
  int t = 0;
  for (int i = 0; i < 4; i = i + 1) {
    t = t + m[i * 4 + i];
  }
  return t;
}

int main() {
  int a[16];
  int b[16];
  int c[16];
  for (int i = 0; i < 16; i = i + 1) {
    a[i] = i;
    b[i] = 16 - i;
  }
  matmul(a, b, c);
  return trace(c);
}
