// Deliberately broken, for the diagnostics beat of the demo.
// Five distinct fault classes, one per line.
int classify(int count, bool done) {
  int total = done;            // E0201  int initialised from bool
  if (count) {                 // E0203  int used as a condition
    return true;               // E0402  bool returned from an int function
  }
  bool flag = count + 1;       // E0201  bool initialised from int
  return conut;                // E0101  misspelled identifier
}
