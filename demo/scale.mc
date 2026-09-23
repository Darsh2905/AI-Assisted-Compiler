// Every construct here exercises a different part of the pipeline.
int shift_scale(int x, int n) {
  int doubled = x + x;         // a rewrite that is proven but NOT profitable
  int scaled  = doubled * 16;  // a rewrite that is proven AND profitable
  if (n != 0 && scaled / n > 10) {   // short-circuit: guards a trapping divide
    return scaled / n;
  }
  return scaled;
}
