# Cross-Validation & Page Connection Engine

This document explains the two engines that compute `cross_validation_results` and `connections` at query time, written so you can re-implement them in JavaScript/TypeScript.

---

## TypeScript interfaces

```ts
// Input
interface OCRLine {
  text: string;
  confidence: number;
  bbox: number[];        // [x1, y1, x2, y2]
  polygon: number[][];
}

interface OCRPage {
  paged_idx: number;           // 1-based
  page_type: string | null;    // "MAWB" | "HAWB" | "IATA" | "INVOICE" | "CARGO_MANIFEST" | "UNKNOWN"
  sub_page_type: string | null;
  page_confidence: number | null;
  image_bbox: number[];
  raw_text: string | null;
  text_lines: OCRLine[];
  checklist: Record<string, unknown> | null;
}

// Output — cross-validation
interface ValidationResult {
  category: string | null;
  rule_name: string;
  status: "pass" | "fail" | "skipped";
  expected: unknown;
  actual: unknown;
  message: string;
}

// Output — page connections
interface PageConnection {
  from: number;   // paged_idx of the source page
  to: number;     // paged_idx of the linked page
  confidence: number | null;
}
```

---

## Part 1 — Cross-Validation Engine

### Entry point

```ts
runCrossValidation(pages: OCRPage[]): ValidationResult[]
```

Takes all pages from one OCRDocument, runs every rule in `CROSS_VALIDATION_RULES`, returns one `ValidationResult` per rule (same order as the config). Never throws.

---

### Step 1 — Group pages by type

```ts
function pagesByType(pages: OCRPage[]): Map<string, OCRPage[]> {
  const map = new Map<string, OCRPage[]>();
  for (const page of pages) {
    const pt = (page.page_type ?? "").trim().toUpperCase();
    if (!pt) continue;
    if (!map.has(pt)) map.set(pt, []);
    map.get(pt)!.push(page);
  }
  return map;
}
```

---

### Step 2 — Merge pages of the same type

A logical document (e.g. a CARGO_MANIFEST) may span several physical pages. Before evaluating any rule, all pages of the same type are merged into one virtual document.

**Merge strategy:**
- **Scalar fields** → first non-null value wins (summary totals appear once per doc)
- **List fields** → concatenated in page order (paginated tables like `hawb_list`, `freight_numbers` grow across pages)

```ts
function mergePages(pages: OCRPage[]): { checklist: Record<string, unknown> } {
  const merged: Record<string, unknown> = {};
  for (const page of pages) {
    const cl = page.checklist ?? {};
    for (const [key, value] of Object.entries(cl)) {
      const existing = merged[key];
      if (existing === undefined || existing === null) {
        // First time seeing this key — take whatever it is
        merged[key] = value;
      } else if (Array.isArray(existing) && Array.isArray(value)) {
        // Both sides are lists — concatenate
        merged[key] = [...existing, ...value];
      }
      // Scalar already set — keep the first non-null value, ignore this one
    }
  }
  return { checklist: merged };
}

function mergedByType(
  byType: Map<string, OCRPage[]>
): Map<string, { checklist: Record<string, unknown> }> {
  const result = new Map();
  for (const [type, pages] of byType) {
    result.set(type, mergePages(pages));
  }
  return result;
}
```

---

### Step 3 — Resolve dot-notation keys

Rules reference checklist fields using dot paths like `"checklist.total_weight"` or `"checklist.hawb_list"`.

```ts
function resolvePath(obj: unknown, dottedKey: string): unknown {
  let cur = obj;
  for (const part of dottedKey.split(".")) {
    if (cur === null || cur === undefined || typeof cur !== "object") return null;
    cur = (cur as Record<string, unknown>)[part] ?? null;
  }
  return cur;
}
```

---

### Step 4 — Rule evaluators

#### 4a. `match` — direct field equality

Compares `merged(doc_a)[key_a]` with `merged(doc_b)[key_b]`.
For numbers: passes if `|actual - expected| <= tolerance`.
For strings: passes if trimmed strings are equal.

```ts
function evalMatch(rule, byType, merged): ValidationResult {
  const docA = rule.doc_a.toUpperCase();
  const docB = rule.doc_b.toUpperCase();
  const tolerance = rule.tolerance ?? 0;

  if (!merged.has(docA))
    return skip(rule, `No ${docA} page found`);
  if (!merged.has(docB))
    return skip(rule, `No ${docB} page found`);

  const valA = resolvePath(merged.get(docA), rule.key_a);
  const valB = resolvePath(merged.get(docB), rule.key_b);

  if (valA === null) return skip(rule, `${docA}: "${rule.key_a}" is missing`);
  if (valB === null) return skip(rule, `${docB}: "${rule.key_b}" is missing`);

  const numA = toFloat(valA);
  const numB = toFloat(valB);

  if (numA !== null && numB !== null) {
    const passed = Math.abs(numA - numB) <= Math.max(tolerance, 0);
    return {
      category: rule.category,
      rule_name: rule.name,
      status: passed ? "pass" : "fail",
      expected: numB,
      actual: numA,
      message: passed ? "" : `Difference ${Math.abs(numA - numB).toFixed(4)} exceeds tolerance ${tolerance}`,
    };
  }

  // String comparison
  const passed = String(valA).trim() === String(valB).trim();
  return result(rule, passed ? "pass" : "fail", valB, valA,
    passed ? "" : `Expected '${valB}', got '${valA}'`);
}
```

---

#### 4b. `sum_match` — sum across individual pages of doc_a

Used when multiple HAWB pages must collectively equal the MAWB total.

**Important:** Uses **individual pages** (not merged) for `doc_a` — each page contributes its own value.
Uses the **merged** view for `doc_b` so a total on the last manifest page is found.

```ts
function evalSumMatch(rule, byType, merged): ValidationResult {
  const docA = rule.doc_a.toUpperCase();
  const docB = rule.doc_b.toUpperCase();
  const tolerance = rule.tolerance ?? 0;

  const pagesA = byType.get(docA) ?? [];
  if (pagesA.length === 0) return skip(rule, `No ${docA} pages found`);
  if (!merged.has(docB))   return skip(rule, `No ${docB} page found`);

  let total = 0;
  let missing = 0;
  for (const page of pagesA) {
    const num = toFloat(resolvePath(page, rule.key_a));  // resolve on raw page dict
    if (num === null) { missing++; } else { total += num; }
  }

  if (missing === pagesA.length)
    return skip(rule, `All ${pagesA.length} ${docA} page(s) are missing "${rule.key_a}"`);

  const expected = toFloat(resolvePath(merged.get(docB), rule.key_b));
  if (expected === null)
    return skip(rule, `${docB}: "${rule.key_b}" is missing or not numeric`);

  const passed = Math.abs(total - expected) <= Math.max(tolerance, 0);
  return result(rule, passed ? "pass" : "fail", expected, total, ...);
}
```

---

#### 4c. `array_sum_match` — sum a field inside a nested list

Used for summing `hawb_list[].weight_kg` vs `total_weight` on the same manifest.
Uses the **merged** doc for `doc_a` so a paginated `hawb_list` is fully assembled first.

```ts
function evalArraySumMatch(rule, byType, merged): ValidationResult {
  const docA = rule.doc_a.toUpperCase();
  const docB = rule.doc_b.toUpperCase();
  const tolerance = rule.tolerance ?? 0;
  const arrayKey = rule.array_key;  // e.g. "weight_kg" or "pcs"

  if (!merged.has(docA)) return skip(rule, `No ${docA} page found`);
  if (!merged.has(docB)) return skip(rule, `No ${docB} page found`);

  const array = resolvePath(merged.get(docA), rule.key_a);
  if (!Array.isArray(array) || array.length === 0)
    return skip(rule, `${docA}: "${rule.key_a}" is not a non-empty list`);

  let total = 0;
  let missing = 0;
  for (const item of array) {
    if (typeof item !== "object" || item === null) { missing++; continue; }
    const num = toFloat((item as Record<string, unknown>)[arrayKey]);
    if (num === null) { missing++; } else { total += num; }
  }

  if (missing === array.length)
    return skip(rule, `All items in "${rule.key_a}" are missing "${arrayKey}"`);

  const expected = toFloat(resolvePath(merged.get(docB), rule.key_b));
  if (expected === null)
    return skip(rule, `${docB}: "${rule.key_b}" is missing or not numeric`);

  const passed = Math.abs(total - expected) <= Math.max(tolerance, 0);
  return result(rule, passed ? "pass" : "fail", expected, total, ...);
}
```

---

#### 4d. `list_match` — set equality between two lists

Order-insensitive, case-insensitive. Both sides are normalized to trimmed uppercase strings.
Either list may be flat strings or a list of objects — use `array_key_a` / `array_key_b` to pluck a scalar from objects.

```ts
function evalListMatch(rule, byType, merged): ValidationResult {
  const docA = rule.doc_a.toUpperCase();
  const docB = rule.doc_b.toUpperCase();

  if (!merged.has(docA)) return skip(rule, `No ${docA} page found`);
  if (!merged.has(docB)) return skip(rule, `No ${docB} page found`);

  const listA = extractList(merged.get(docA), rule.key_a, rule.array_key_a);
  const listB = extractList(merged.get(docB), rule.key_b, rule.array_key_b);

  if (listA === null) return skip(rule, `${docA}: "${rule.key_a}" is missing or not a list`);
  if (listB === null) return skip(rule, `${docB}: "${rule.key_b}" is missing or not a list`);
  if (!listA.length && !listB.length) return skip(rule, "Both lists are empty");
  if (!listA.length) return skip(rule, `${docA}: "${rule.key_a}" is empty`);
  if (!listB.length) return skip(rule, `${docB}: "${rule.key_b}" is empty`);

  const setA = new Set(listA);
  const setB = new Set(listB);
  const onlyInA = [...setA].filter(x => !setB.has(x)).sort();
  const onlyInB = [...setB].filter(x => !setA.has(x)).sort();
  const passed = onlyInA.length === 0 && onlyInB.length === 0;

  const parts = [];
  if (onlyInA.length) parts.push(`In ${docA} only: ${JSON.stringify(onlyInA)}`);
  if (onlyInB.length) parts.push(`In ${docB} only: ${JSON.stringify(onlyInB)}`);

  return {
    category: rule.category,
    rule_name: rule.name,
    status: passed ? "pass" : "fail",
    expected: [...setB].sort(),
    actual: [...setA].sort(),
    message: parts.join("  "),
  };
}

// Helper: resolve list from a merged doc and normalize to uppercase strings
function extractList(
  doc: unknown,
  key: string,
  arrayKey: string | null | undefined
): string[] | null {
  const raw = resolvePath(doc, key);
  if (!Array.isArray(raw)) return null;
  const result: string[] = [];
  for (const item of raw) {
    let val: unknown;
    if (typeof item === "object" && item !== null) {
      if (!arrayKey) continue;
      val = (item as Record<string, unknown>)[arrayKey];
    } else {
      val = item;
    }
    if (val !== null && val !== undefined) {
      const s = String(val).trim().toUpperCase();
      if (s) result.push(s);
    }
  }
  return result;
}
```

---

### Step 5 — Main entry point

```ts
function runCrossValidation(pages: OCRPage[]): ValidationResult[] {
  const byType = pagesByType(pages);
  const merged = mergedByType(byType);

  const evaluators: Record<string, Function> = {
    match: evalMatch,
    sum_match: evalSumMatch,
    array_sum_match: evalArraySumMatch,
    list_match: evalListMatch,
  };

  return CROSS_VALIDATION_RULES.map(rule => {
    const evaluator = evaluators[rule.type];
    if (!evaluator) {
      return { category: rule.category, rule_name: rule.name, status: "skipped",
               expected: null, actual: null, message: `Unknown rule type: "${rule.type}"` };
    }
    try {
      const result = evaluator(rule, byType, merged);
      result.category = rule.category ?? null;  // always attach category
      return result;
    } catch (e) {
      return { category: rule.category, rule_name: rule.name, status: "skipped",
               expected: null, actual: null, message: "Internal error" };
    }
  });
}
```

---

### Shared helpers

```ts
function toFloat(value: unknown): number | null {
  const n = Number(value);
  return isNaN(n) ? null : n;
}

function skip(rule: any, message: string): ValidationResult {
  return { category: rule.category, rule_name: rule.name,
           status: "skipped", expected: null, actual: null, message };
}
```

---

### Active rules (from config)

#### How `category` works

`category` is a **static string defined on each rule** in `cross_validation_config.py`. It is not derived from the documents at runtime. After an evaluator returns a `ValidationResult`, the engine stamps `category` onto it with `result.model_copy(update={"category": category})`. The evaluators themselves (`_eval_match`, `_eval_sum_match`, etc.) never set `category`.

To add a new grouping, just set `"category"` on the rule dict — no code changes elsewhere are needed.

#### Rule table

| Category | Rule name | Type | doc_a field | doc_b field | Tolerance |
|---|---|---|---|---|---|
| `MAWB vs Manifest` | MAWB vs Manifest: Total Weight | `match` | `MAWB.checklist.total_weight` | `CARGO_MANIFEST.checklist.total_weight` | 0.5 |
| `MAWB vs Manifest` | MAWB vs Manifest: Freight Numbers | `list_match` | `MAWB.checklist.freight_numbers[]` | `CARGO_MANIFEST.checklist.hawb_list[].hawb_no` | — |
| `MAWB vs HAWB` | MAWB vs HAWB: Total Weight | `match` | `MAWB.checklist.total_weight` | `HAWB.checklist.total_weight` | 0.5 |
| `IATA vs Manifest` | IATA vs Manifest: Total Weight | `match` | `IATA.checklist.total_weight` | `CARGO_MANIFEST.checklist.total_weight` | 0.5 |
| `HAWB vs Manifest` | HAWB vs Manifest: Total Weight | `sum_match` | sum(`HAWB[].checklist.total_weight`) | `CARGO_MANIFEST.checklist.total_weight` | 0.5 |
| `HAWB vs MAWB` | HAWB vs MAWB: Total Weight | `sum_match` | sum(`HAWB[].checklist.total_weight`) | `MAWB.checklist.total_weight` | 0.5 |
| `Manifest: Internal` | Manifest: HAWB List Weight Sum vs Total Weight | `array_sum_match` | `CARGO_MANIFEST.checklist.hawb_list[].weight_kg` | `CARGO_MANIFEST.checklist.total_weight` | 0.5 |
| `Manifest: Internal` | Manifest: HAWB List Pieces Sum vs Total Pieces | `array_sum_match` | `CARGO_MANIFEST.checklist.hawb_list[].pcs` | `CARGO_MANIFEST.checklist.total_pcs` | 0 |

---

## Part 2 — Page Connection Engine

### Entry point

```ts
buildPageConnections(pages: OCRPage[]): PageConnection[]
```

Returns directed links between pages that belong to the same shipment. Never throws.

---

### How it works

1. Group pages by `page_type` — **only pages where `checklist !== null` are included**
2. For each connection rule, get the `from` pages and `to` pages
3. If either side is empty, skip the rule
4. For each `(fromPage, toPage)` pair, test if they match
5. If matched, emit `{ from: fromPage.paged_idx, to: toPage.paged_idx, confidence: toPage.page_confidence }`

```ts
function buildPageConnections(pages: OCRPage[]): PageConnection[] {
  // Only pages with a checklist participate
  const byType = new Map<string, OCRPage[]>();
  for (const page of pages) {
    const pt = (page.page_type ?? "").trim().toUpperCase();
    if (!pt || page.checklist === null) continue;
    if (!byType.has(pt)) byType.set(pt, []);
    byType.get(pt)!.push(page);
  }

  const all: PageConnection[] = [];
  for (const rule of CONNECTION_RULES) {
    const fromPages = byType.get(rule.from_type.toUpperCase()) ?? [];
    const toPages   = byType.get(rule.to_type.toUpperCase())   ?? [];
    if (!fromPages.length || !toPages.length) continue;

    try {
      all.push(...evalConnectionRule(rule, fromPages, toPages));
    } catch { continue; }
  }
  return all;
}
```

---

### Rule evaluator

```ts
function evalConnectionRule(
  rule: ConnectionRule,
  fromPages: OCRPage[],
  toPages: OCRPage[],
): PageConnection[] {
  const connections: PageConnection[] = [];

  for (const fromPage of fromPages) {
    const fromSet = extractScalars(
      fromPage.checklist!, rule.from_key, rule.from_array_key);
    if (fromSet.size === 0) continue;

    for (const toPage of toPages) {
      // Skip self-connections
      if (toPage.paged_idx === fromPage.paged_idx) continue;
      // For same-type rules (e.g. INVOICE→INVOICE), only emit each pair once
      if (rule.from_type.toUpperCase() === rule.to_type.toUpperCase()
          && toPage.paged_idx < fromPage.paged_idx) continue;

      const toSet = extractScalars(
        toPage.checklist!, rule.to_key, rule.to_array_key);
      if (toSet.size === 0) continue;

      let matched = false;
      if (rule.type === "list_overlap") {
        matched = [...fromSet].some(x => toSet.has(x));  // set intersection non-empty
      } else if (rule.type === "key_match") {
        matched = fromSet.size === toSet.size && [...fromSet].every(x => toSet.has(x));
      }

      if (matched) {
        connections.push({
          from: fromPage.paged_idx,
          to: toPage.paged_idx,
          confidence: toPage.page_confidence,
        });
      }
    }
  }
  return connections;
}
```

---

### Scalar extraction helper

```ts
function extractScalars(
  checklist: Record<string, unknown>,
  key: string,
  arrayKey: string | null | undefined,
): Set<string> {
  const val = checklist[key];
  if (val === null || val === undefined) return new Set();

  if (Array.isArray(val)) {
    const result = new Set<string>();
    for (const item of val) {
      let v: unknown;
      if (typeof item === "object" && item !== null) {
        if (!arrayKey) continue;
        v = (item as Record<string, unknown>)[arrayKey];
      } else {
        v = item;
      }
      if (v !== null && v !== undefined) {
        const s = String(v).trim().toUpperCase();
        if (s) result.add(s);
      }
    }
    return result;
  }

  // Scalar
  const s = String(val).trim().toUpperCase();
  return s ? new Set([s]) : new Set();
}
```

---

### Active connection rules

| Rule | Type | from | to | Match logic |
|---|---|---|---|---|
| MAWB → CARGO_MANIFEST | `list_overlap` | `MAWB.checklist.freight_numbers[]` | `CARGO_MANIFEST.checklist.hawb_list[].hawb_no` | Any HAWB number on the MAWB appears in the manifest |
| CARGO_MANIFEST → HAWB | `list_overlap` | `CARGO_MANIFEST.checklist.hawb_list[].hawb_no` | `HAWB.checklist.awb_number` | HAWB's AWB number appears in the manifest's list |
| HAWB → IATA | `key_match` | `HAWB.checklist.awb_number` | `IATA.checklist.awb_number` | Exact scalar equality |
| INVOICE → INVOICE | `key_match` | `INVOICE.checklist.invoice_no` | `INVOICE.checklist.invoice_no` | Same invoice number (different pages of same invoice) |
| HAWB → INVOICE | `key_match` | `HAWB.checklist.awb_number` | `INVOICE.checklist.awb_number` | AWB number on HAWB equals AWB number on INVOICE |

---

## Key differences between the two engines

| | Cross-Validation | Page Connections |
|---|---|---|
| Entry point | `runCrossValidation(pages)` | `buildPageConnections(pages)` |
| Includes pages where `checklist = null`? | Yes (checklist is accessed via merge) | No (filtered out upfront) |
| Multi-page merge | Yes — scalars take first non-null, lists concatenate | No — each page evaluated individually |
| `sum_match` uses merged? | No — intentionally uses raw individual pages for `doc_a` | N/A |
| Output per rule | Always one `ValidationResult` (even if skipped) | Zero or more `PageConnection` objects |
| On error | Returns `"skipped"` result | Skips the rule, continues |
