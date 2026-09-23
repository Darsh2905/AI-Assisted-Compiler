// Bubble sort plus a binary search over the sorted result.
// Exercises short-circuit `&&` (and therefore its trap semantics), nested
// loops, and array bounds that the checked accesses must respect.
void bubble_sort(int xs[12], int n) {
  for (int i = 0; i < n; i = i + 1) {
    for (int j = 0; j < n - 1 - i; j = j + 1) {
      if (xs[j] > xs[j + 1]) {
        int t = xs[j];
        xs[j] = xs[j + 1];
        xs[j + 1] = t;
      }
    }
  }
}

int binary_search(int xs[12], int n, int key) {
  int lo = 0;
  int hi = n - 1;
  while (lo <= hi) {
    int mid = lo + (hi - lo) / 2;
    if (xs[mid] == key) {
      return mid;
    } else {
      if (xs[mid] < key) {
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
  }
  return 0 - 1;
}

bool in_range(int xs[12], int n, int i) {
  return 0 <= i && i < n && xs[i] >= 0;
}

int main() {
  int xs[12];
  for (int i = 0; i < 12; i = i + 1) {
    xs[i] = (12 - i) * 7 % 13;
  }
  bubble_sort(xs, 12);
  if (in_range(xs, 12, 3)) {
    return binary_search(xs, 12, xs[3]);
  }
  return 0 - 1;
}
