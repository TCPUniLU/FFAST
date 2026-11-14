//! The fast parser for .xyz / extended-XYZ files (ADR 0100).
//!
//! `parse_into` fills arrays that Python made at full size from a group of
//! frames, on several threads, with Python's lock released. It copies the
//! rules of FFAST's Python parser (`ffast/io/extxyz_arrays.py`,
//! `parse_block`) exactly. Where it cannot be sure to give the same value it
//! raises `NotFast`, and FFAST reads the file with the Python parser:
//!
//! - numbers: only plain decimal text (`-1.5e3`, `.5`, `7`) is read here; the
//!   rest (`1_000`, `nan`, `0x10`, a 128-bit integer) is the Python parser's.
//!   A header value that might be such a number comes back as text, for Python
//!   to convert; an atom line with one hands the file over;
//! - non-ASCII text outside a quoted header value, unusual column layouts, and
//!   a frame whose columns differ from frame 0's are handed over.
//!
//! Every thread writes only its own slices of the arrays and never calls into
//! Python.

// pyo3 0.22's create_exception! checks a cfg this crate does not declare.
#![allow(unexpected_cfgs)]

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use memmap2::Mmap;
use numpy::{
    PyArray1, PyArrayMethods, PyReadonlyArray1, PyReadwriteArray1, PyReadwriteArray2,
    PyReadwriteArray3, PyReadwriteArrayDyn, PyUntypedArrayMethods,
};
use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;

create_exception!(
    _ffast_xyz,
    NotFast,
    PyException,
    "The fast parser hands this file to the Python parser; the message says why."
);

/// The version of the `parse_into` contract this package follows.
const CONTRACT: u32 = 1;

const PLAIN: &[u8] = b"species:S:1:pos:R:3";

// ── characters and numbers ────────────────────────────────────────────────

/// What `bytes.split()` and `bytes.strip()` treat as whitespace.
fn line_space(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

/// What `\s` matches in a Python `str` pattern, for ASCII text.
fn re_space(b: u8) -> bool {
    line_space(b) || (0x1c..=0x1f).contains(&b)
}

fn word(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

fn digits(t: &[u8], mut i: usize) -> usize {
    while i < t.len() && t[i].is_ascii_digit() {
        i += 1;
    }
    i
}

fn sign(t: &[u8]) -> usize {
    usize::from(!t.is_empty() && (t[0] == b'+' || t[0] == b'-'))
}

/// `[+-]?[0-9]+`
fn int_text(t: &[u8]) -> bool {
    let s = sign(t);
    s < t.len() && digits(t, s) == t.len()
}

/// `[+-]?(d+.?d*|.d+)([eE][+-]?d+)?`: what Rust and Python read alike.
fn float_text(t: &[u8]) -> bool {
    let s = sign(t);
    let mut i = digits(t, s);
    let mut seen = i - s;
    if i < t.len() && t[i] == b'.' {
        let j = digits(t, i + 1);
        seen += j - i - 1;
        i = j;
    }
    if seen == 0 {
        return false;
    }
    if i < t.len() && (t[i] == b'e' || t[i] == b'E') {
        let e = i + 1 + sign(&t[i + 1..]);
        let j = digits(t, e);
        if j == e {
            return false;
        }
        i = j;
    }
    i == t.len()
}

fn as_str(t: &[u8]) -> &str {
    // Only called on text float_text/int_text accepted: ASCII.
    std::str::from_utf8(t).unwrap_or("")
}

/// A number in an atom line, or None when only Python can read it.
fn atom_float(t: &[u8]) -> Option<f64> {
    if float_text(t) {
        as_str(t).parse().ok()
    } else {
        None
    }
}

fn atom_int(t: &[u8]) -> Option<i32> {
    if int_text(t) {
        as_str(t).parse().ok()
    } else {
        None
    }
}

/// A header value as `_number` reads it (int, then float), or Text when only
/// Python can tell (`1_000`, ` 5 `, a number beyond 64 bits, `nan`), or None
/// when it cannot be a number.
enum Num {
    I(i64),
    F(f64),
    T(String),
}

fn header_number(v: &[u8]) -> Option<Num> {
    if int_text(v) {
        return Some(match as_str(v).parse::<i64>() {
            Ok(x) => Num::I(x),
            Err(_) => Num::T(as_str(v).to_string()),
        });
    }
    if float_text(v) {
        return as_str(v).parse::<f64>().ok().map(Num::F);
    }
    if maybe_number(v) {
        Some(Num::T(String::from_utf8_lossy(v).into_owned()))
    } else {
        None
    }
}

/// Whether Python's int() or float() might read `v`: they take spaces around
/// it, a sign, digits (any script), `_`, `.`, an exponent, or inf, infinity,
/// nan in any case; nothing else, and no space inside. Non-ASCII text is
/// Python's to judge.
fn maybe_number(v: &[u8]) -> bool {
    if v.iter().any(|&b| b >= 0x80) {
        return true;
    }
    let t = strip(v);
    let allowed = |b: &u8| {
        b.is_ascii_digit() || matches!(b.to_ascii_lowercase(), b'+' | b'-' | b'_' | b'.' | b'e' | b'i' | b'n' | b'f' | b't' | b'y' | b'a')
    };
    // str.strip() also strips \x1c-\x1f; such a value is too odd to judge here.
    !t.is_empty() && (t.iter().all(allowed) || t.iter().any(|&b| (0x1c..=0x1f).contains(&b)))
        && (t.iter().any(u8::is_ascii_digit) || {
            let lower = t.to_ascii_lowercase();
            lower.windows(3).any(|w| w == b"inf" || w == b"nan")
        })
}

fn shown(t: &[u8]) -> String {
    format!("'{}'", String::from_utf8_lossy(t))
}

// ── the header line: `_PAIR`, `_value`, `_cell`, `_pbc` ────────────────────

enum Tok<'a> {
    Pair(&'a [u8], Option<&'a [u8]>),
    Junk(&'a [u8]),
}

/// The next match of `_PAIR` in `h` from `*i`: a key with an optional
/// `=value` (bare, quoted, braced or bracketed) ending at whitespace, or else
/// the junk up to the next whitespace. Err: non-ASCII text the ASCII rules
/// here cannot be sure of.
fn next_tok<'a>(h: &'a [u8], i: &mut usize) -> Result<Option<Tok<'a>>, String> {
    let n = h.len();
    while *i < n && re_space(h[*i]) {
        *i += 1;
    }
    if *i >= n {
        return Ok(None);
    }
    let s = *i;
    let junk = |i: &mut usize| {
        let mut j = s;
        while j < n && !re_space(h[j]) {
            j += 1;
        }
        *i = j;
        Ok(Some(Tok::Junk(&h[s..j])))
    };
    if !(h[s].is_ascii_alphabetic() || h[s] == b'_') {
        if h[s] >= 0x80 {
            return Err("non-ASCII text in its header".into());
        }
        return junk(i);
    }
    let mut j = s + 1;
    while j < n && word(h[j]) {
        j += 1;
    }
    if j < n && h[j] >= 0x80 {
        return Err("non-ASCII text in its header".into());
    }
    let key = &h[s..j];
    if j == n || re_space(h[j]) {
        *i = j;
        return Ok(Some(Tok::Pair(key, None)));
    }
    if h[j] != b'=' || j + 1 == n {
        return junk(i);
    }
    let v = j + 1;
    let end = match h[v] {
        q @ (b'"' | b'\'') => {
            let mut k = v + 1;
            loop {
                if k >= n {
                    return junk(i);
                }
                if h[k] == b'\\' {
                    if k + 1 >= n {
                        return junk(i);
                    }
                    k += 2;
                    continue;
                }
                if h[k] == q {
                    break k + 1;
                }
                k += 1;
            }
        }
        b'{' | b'[' => {
            let close = if h[v] == b'{' { b'}' } else { b']' };
            match h[v + 1..].iter().position(|&b| b == close) {
                Some(p) => v + 1 + p + 1,
                None => return junk(i),
            }
        }
        c if re_space(c) => return junk(i),
        _ => {
            let mut k = v;
            while k < n && !re_space(h[k]) {
                if h[k] >= 0x80 {
                    return Err("non-ASCII text in its header".into());
                }
                k += 1;
            }
            k
        }
    };
    if end < n && !re_space(h[end]) {
        return junk(i);
    }
    *i = end;
    Ok(Some(Tok::Pair(key, Some(&h[v..end]))))
}

/// `_value`: a quoted, braced or bracketed value without its ends, its
/// backslash escapes undone; a bare value as it is.
fn value(raw: &[u8]) -> Cow<'_, [u8]> {
    if !matches!(raw[0], b'"' | b'\'' | b'{' | b'[') {
        return Cow::Borrowed(raw);
    }
    let inner = &raw[1..raw.len() - 1];
    if !inner.contains(&b'\\') {
        return Cow::Borrowed(inner);
    }
    let mut out = Vec::with_capacity(inner.len());
    let mut k = 0;
    while k < inner.len() {
        if inner[k] == b'\\' && k + 1 < inner.len() {
            out.push(inner[k + 1]);
            k += 2;
        } else {
            out.push(inner[k]);
            k += 1;
        }
    }
    Cow::Owned(out)
}

/// `_TOKENS` (`[^\s,]+`) of an ASCII value.
fn tokens(v: &[u8]) -> Result<Vec<&[u8]>, String> {
    if v.iter().any(|&b| b >= 0x80) {
        return Err("non-ASCII text in a Lattice or pbc value".into());
    }
    Ok(v.split(|&b| re_space(b) || b == b',').filter(|t| !t.is_empty()).collect())
}

fn lattice(v: &[u8]) -> Result<[f64; 9], String> {
    if v.iter().any(|&b| b >= 0x80) {
        return Err("non-ASCII text in a Lattice value".into());
    }
    let mut cell = [0.0; 9];
    let mut n = 0;
    for t in v.split(|&b| re_space(b) || b == b',').filter(|t| !t.is_empty()) {
        if n == 9 {
            return Err("Lattice is not 9 numbers".into());
        }
        cell[n] = atom_float(t).ok_or_else(|| format!("Lattice holds {}", shown(t)))?;
        n += 1;
    }
    if n != 9 {
        return Err("Lattice is not 9 numbers".into());
    }
    Ok(cell)
}

fn pbc(v: &[u8]) -> Result<(), String> {
    let toks = tokens(v)?;
    let ok = toks.iter().all(|t| {
        matches!(*t, b"T" | b"F" | b"true" | b"false" | b"True" | b"False" | b"TRUE" | b"FALSE")
    });
    if ok && (toks.len() == 1 || toks.len() == 3) {
        Ok(())
    } else {
        Err(format!("pbc={}", shown(v)))
    }
}

/// Whether a header without `=` is a plain comment: none of its words is
/// Properties, Lattice or pbc.
fn plain(h: &[u8]) -> Result<bool, String> {
    if h.contains(&b'=') {
        return Ok(false);
    }
    let meaningful = |w: &[u8]| w == b"Properties" || w == b"Lattice" || w == b"pbc";
    if h.iter().any(|&b| b >= 0x80) {
        // str.split() also splits at non-ASCII spaces: sure only without the words.
        let has = [&b"Properties"[..], b"Lattice", b"pbc"].iter().any(|w| h.windows(w.len()).any(|x| x == *w));
        return if has { Err("non-ASCII text in its header".into()) } else { Ok(true) };
    }
    Ok(!h.split(|&b| re_space(b)).any(meaningful))
}

// ── column layouts: `_layout`, `_signature`, `_is_extra` ───────────────────

#[derive(Clone, Copy, PartialEq)]
enum Role {
    Species,
    Z,
    Pos,
    Target(usize),
    NewForces,
    NewField,
    Ignore,
}

struct Col {
    name: String,
    kind: u8,
    start: usize,
    n: usize,
    role: Role,
    new: usize, // index among the layout's new columns
}

struct Layout {
    ncol: usize,
    cols: Vec<Col>,
    essential: Vec<(String, u8, usize)>,
    extras: Vec<(String, u8, usize)>,
}

fn is_extra(name: &str, kind: u8, n: usize) -> bool {
    n == 1 && (kind == b'R' || kind == b'I') && !matches!(name, "species" | "Z" | "pos")
}

fn layout_of(props: &[u8], ctx: &Ctx) -> Result<Layout, String> {
    let text = std::str::from_utf8(props)
        .ok()
        .filter(|t| t.is_ascii())
        .ok_or("non-ASCII text in Properties")?;
    let parts: Vec<&str> = text.split(':').collect();
    if parts.len() % 3 != 0 {
        return Err(format!("Properties={text} is not name:type:count triples"));
    }
    let mut cols = Vec::new();
    let mut names = HashSet::new();
    let mut start = 0;
    for t in parts.chunks(3) {
        let (name, kind, count) = (t[0], t[1].as_bytes(), t[2].as_bytes());
        if !int_text(count) || count[0] == b'+' || count[0] == b'-' {
            return Err(format!("Properties={text}: count {}", t[2]));
        }
        let n: usize = t[2].parse().map_err(|_| format!("Properties={text}: count {}", t[2]))?;
        if kind.len() != 1 || !matches!(kind[0], b'R' | b'I' | b'S') || n == 0 {
            return Err(format!("Properties={text}: column {name} of type {}", t[1]));
        }
        if !names.insert(name) {
            return Err(format!("Properties={text}: column {name} twice"));
        }
        cols.push(Col { name: name.to_string(), kind: kind[0], start, n, role: Role::Ignore, new: 0 });
        start += n;
    }
    if !names.contains("pos") || !(names.contains("species") || names.contains("Z")) {
        return Err(format!("Properties={text} has no positions or elements"));
    }
    if names.contains("species") && names.contains("Z") {
        return Err(format!("Properties={text} has both species and Z"));
    }
    let mut new = 0;
    for c in cols.iter_mut() {
        let odd = || format!("Properties={text}: column {} of type {}:{}", c.name, c.kind as char, c.n);
        let target = ctx.targets.iter().position(|(t, _)| *t == c.name);
        c.role = match (c.name.as_str(), c.kind, c.n) {
            ("species", b'S', 1) => Role::Species,
            ("Z", b'I', 1) => Role::Z,
            ("pos", b'R', 3) => Role::Pos,
            ("species" | "Z" | "pos", _, _) => return Err(odd()),
            (_, b'S', _) => return Err(odd()),
            ("forces", k, 3) if k != b'R' => return Err(odd()),
            ("forces", _, n) if n != 3 => return Err(odd()),
            _ => match target {
                Some(t) => {
                    let width = ctx.targets[t].1;
                    if c.n != width || (width == 3 && c.kind != b'R') {
                        return Err(odd());
                    }
                    Role::Target(t)
                }
                None if c.n == 3 && c.name.to_ascii_lowercase().contains("force") => {
                    if c.kind != b'R' {
                        return Err(odd()); // the Python parser keeps it as int32
                    }
                    Role::NewForces
                }
                None if c.n == 1 && !ctx.excluded(&c.name) => Role::NewField,
                None => Role::Ignore,
            },
        };
        if matches!(c.role, Role::NewForces | Role::NewField) {
            c.new = new;
            new += 1;
        }
    }
    let mut essential = Vec::new();
    let mut extras = Vec::new();
    for c in &cols {
        let spec = (c.name.clone(), c.kind, c.n);
        if is_extra(&c.name, c.kind, c.n) {
            extras.push(spec);
        } else {
            essential.push(spec);
        }
    }
    Ok(Layout { ncol: start, cols, essential, extras })
}

// ── one group: shared input, per-thread output ─────────────────────────────

struct Ctx<'a> {
    data: &'a [u8],
    starts: &'a [u64],
    ends: &'a [u64],
    counts: &'a [i64],
    first_frame: i64,
    elements: &'a HashMap<Vec<u8>, i64>,
    rev: &'a HashMap<String, String>,
    excluded_names: &'a HashSet<String>,
    targets: &'a [(String, usize)],
    reference: &'a [(String, u8, usize)],
    known_extras: &'a HashMap<String, (u8, usize)>,
}

impl Ctx<'_> {
    /// A 1-wide column the Python parser does not keep as a field: its ASE
    /// name is an ASE property or a reserved array.
    fn excluded(&self, name: &str) -> bool {
        let ase = self.rev.get(name).map(String::as_str).unwrap_or(name);
        self.excluded_names.contains(ase)
    }
}

struct Chunk<'a> {
    a: usize,
    b: usize,
    atom0: usize,
    numbers: &'a mut [i64],
    pos: &'a mut [f64],
    cell: Option<&'a mut [f64]>,
    cols: Vec<&'a mut [f64]>,
}

struct Keyed<T> {
    slot: HashMap<Vec<u8>, usize>,
    keys: Vec<(Vec<u8>, Vec<i64>, Vec<T>)>,
    hint: usize, // frames list their keys in the same order: try the next one first
}

impl<T> Default for Keyed<T> {
    fn default() -> Self {
        Keyed { slot: HashMap::new(), keys: Vec::new(), hint: 0 }
    }
}

impl<T> Keyed<T> {
    fn push(&mut self, key: &[u8], row: i64, x: T) {
        let i = match self.keys.get(self.hint) {
            Some(k) if k.0 == key => self.hint,
            _ => match self.slot.get(key) {
                Some(&i) => i,
                None => {
                    self.slot.insert(key.to_vec(), self.keys.len());
                    self.keys.push((key.to_vec(), Vec::new(), Vec::new()));
                    self.keys.len() - 1
                }
            },
        };
        self.hint = if i + 1 < self.keys.len() { i + 1 } else { 0 };
        self.keys[i].1.push(row);
        self.keys[i].2.push(x);
    }

    fn extend(&mut self, other: Keyed<T>) {
        for (key, rows, xs) in other.keys {
            for (r, x) in rows.into_iter().zip(xs) {
                self.push(&key, r, x);
            }
        }
    }
}

enum Value {
    I(i64),
    F(f64),
}

struct NewCol {
    name: String,
    width: usize,
    rows: Vec<i64>,
    vals: Vec<f64>,
}

#[derive(Default)]
struct Part {
    values: Keyed<Value>,
    texts: Keyed<String>,
    new: Vec<NewCol>,
    seen: Vec<bool>,
    extras: HashMap<String, (u8, usize)>,
}

impl Part {
    fn new_col(&mut self, name: &str, width: usize) -> usize {
        match self.new.iter().position(|c| c.name == name) {
            Some(i) => i,
            None => {
                self.new.push(NewCol { name: name.to_string(), width, rows: Vec::new(), vals: Vec::new() });
                self.new.len() - 1
            }
        }
    }
}

fn strip(t: &[u8]) -> &[u8] {
    let a = t.iter().position(|&b| !line_space(b)).unwrap_or(t.len());
    let b = t.iter().rposition(|&b| !line_space(b)).map_or(a, |p| p + 1);
    &t[a..b]
}

fn parse_chunk(ctx: &Ctx, chunk: Chunk) -> Result<Part, String> {
    let Chunk { a, b, atom0, numbers, pos, mut cell, mut cols } = chunk;
    let mut part = Part { seen: vec![false; ctx.targets.len()], ..Default::default() };
    let mut layouts: Vec<Layout> = Vec::new();
    let mut index: HashMap<Vec<u8>, usize> = HashMap::new(); // Properties -> layout
    let mut current: Option<(Vec<u8>, usize)> = None; // the last frame's
    let mut extras = ctx.known_extras.clone();
    let mut symbols: HashMap<&[u8], i64> = HashMap::new();
    let mut last_symbol: (&[u8], i64) = (b"", 0);
    let mut fields: Vec<(&[u8], Num)> = Vec::with_capacity(32); // one frame's header numbers
    let mut toks: Vec<&[u8]> = Vec::with_capacity(16);
    let mut new_index: Vec<usize> = Vec::new();
    let mut atom = 0usize; // within the chunk
    for row in a..b {
        let frame = ctx.first_frame + row as i64;
        let block = &ctx.data[ctx.starts[row] as usize..ctx.ends[row] as usize];
        let mut lines = block.split(|&c| c == b'\n');
        let count = strip(lines.next().unwrap_or(b""));
        if !int_text(count) || as_str(count).parse::<i64>().ok() != Some(ctx.counts[row]) {
            return Err(format!("frame {frame}: its atom count line {} is not {}", shown(count), ctx.counts[row]));
        }
        let header = lines.next().ok_or_else(|| format!("frame {frame} has no header line"))?;
        std::str::from_utf8(header).map_err(|_| format!("frame {frame}: its header is not UTF-8 text"))?;

        // The header: Properties, Lattice, pbc and the numbers, as parse_block.
        let mut props: Cow<[u8]> = Cow::Borrowed(PLAIN);
        let mut lat: Option<[f64; 9]> = None;
        fields.clear();
        if !plain(header).map_err(|e| format!("frame {frame}: {e}"))? {
            let mut i = 0;
            while let Some(tok) = next_tok(header, &mut i).map_err(|e| format!("frame {frame}: {e}"))? {
                let (key, raw) = match tok {
                    Tok::Junk(t) => return Err(format!("frame {frame}: cannot read {} in its header", shown(t))),
                    Tok::Pair(k, r) => (k, r),
                };
                let Some(raw) = raw else {
                    if matches!(key, b"Properties" | b"Lattice" | b"pbc") {
                        return Err(format!("frame {frame}: {} without a value in its header", as_str(key)));
                    }
                    continue;
                };
                let v = value(raw);
                match key {
                    b"Properties" => props = v,
                    b"Lattice" => lat = Some(lattice(&v).map_err(|e| format!("frame {frame}: {e}"))?),
                    b"pbc" => pbc(&v).map_err(|e| format!("frame {frame}: {e}"))?,
                    k if k.eq_ignore_ascii_case(b"uid") || v.starts_with(b"_JSON ") => {}
                    k => {
                        let Some(num) = header_number(&v) else { continue };
                        match fields.iter_mut().find(|(f, _)| *f == k) {
                            None => fields.push((k, num)),
                            Some((_, old)) => {
                                // Python: a later number wins; a later text
                                // may or may not be one.
                                if matches!(num, Num::T(_)) {
                                    return Err(format!("frame {frame}: {} given twice", as_str(k)));
                                }
                                *old = num;
                            }
                        }
                    }
                }
            }
        }
        for (k, num) in fields.drain(..) {
            match num {
                Num::I(x) => part.values.push(k, row as i64, Value::I(x)),
                Num::F(x) => part.values.push(k, row as i64, Value::F(x)),
                Num::T(t) => part.texts.push(k, row as i64, t),
            }
        }
        let f = row - a;
        if let Some(c) = cell.as_deref_mut() {
            c[f * 9..f * 9 + 9].copy_from_slice(&lat.unwrap_or([0.0; 9]));
        }

        // The columns: the same essential ones as frame 0, extras of one type.
        if current.as_ref().is_none_or(|(p, _)| p.as_slice() != props.as_ref()) {
            let li = match index.get(props.as_ref()) {
                Some(&li) => li,
                None => {
                    let lay = layout_of(&props, ctx).map_err(|e| format!("frame {frame}: {e}"))?;
                    if lay.essential.as_slice() != ctx.reference {
                        return Err(format!("frame {frame} lists the columns {}, unlike frame 0", as_str(&props)));
                    }
                    for (name, kind, n) in &lay.extras {
                        if *extras.entry(name.clone()).or_insert((*kind, *n)) != (*kind, *n) {
                            return Err(format!("frame {frame}: column {name} changes its type"));
                        }
                    }
                    layouts.push(lay);
                    index.insert(props.to_vec(), layouts.len() - 1);
                    layouts.len() - 1
                }
            };
            new_index.clear();
            for c in &layouts[li].cols {
                match c.role {
                    Role::NewForces | Role::NewField => new_index.push(part.new_col(&c.name, c.n)),
                    Role::Target(t) => part.seen[t] = true,
                    _ => {}
                }
            }
            current = Some((props.to_vec(), li));
        }
        let lay = &layouts[current.as_ref().map_or(0, |c| c.1)];

        // The atom lines.
        let n = ctx.counts[row] as usize;
        for _ in 0..n {
            let line = lines.next().ok_or_else(|| format!("frame {frame} has too few atom lines"))?;
            toks.clear();
            toks.extend(line.split(|&c| line_space(c)).filter(|t| !t.is_empty()));
            if toks.len() != lay.ncol {
                return Err(format!("frame {frame}: an atom line has {} columns, not {}", toks.len(), lay.ncol));
            }
            let bad = |t: &[u8]| format!("frame {frame}: {} in an atom line", shown(t));
            for c in &lay.cols {
                let cell_toks = &toks[c.start..c.start + c.n];
                match c.role {
                    Role::Species => {
                        let sym = cell_toks[0];
                        let known = if sym == last_symbol.0 { Some(&last_symbol.1) } else { symbols.get(sym) };
                        let z = match known {
                            Some(&z) => z,
                            None => {
                                if !sym.is_ascii() {
                                    return Err(bad(sym));
                                }
                                let mut key = sym.to_ascii_lowercase();
                                key[0] = key[0].to_ascii_uppercase();
                                let z = *ctx
                                    .elements
                                    .get(&key)
                                    .ok_or_else(|| format!("unknown element '{}'", as_str(&key)))?;
                                symbols.insert(sym, z);
                                z
                            }
                        };
                        numbers[atom] = z;
                        last_symbol = (sym, z);
                    }
                    Role::Z => numbers[atom] = atom_int(cell_toks[0]).ok_or_else(|| bad(cell_toks[0]))? as i64,
                    Role::Pos => {
                        for k in 0..3 {
                            pos[atom * 3 + k] = atom_float(cell_toks[k]).ok_or_else(|| bad(cell_toks[k]))?;
                        }
                    }
                    Role::Target(t) => {
                        let out = &mut cols[t];
                        for k in 0..c.n {
                            out[atom * c.n + k] = number(c.kind, cell_toks[k]).ok_or_else(|| bad(cell_toks[k]))?;
                        }
                    }
                    Role::NewForces | Role::NewField => {
                        let col = &mut part.new[new_index[c.new]];
                        col.rows.push((atom0 + atom) as i64);
                        for t in cell_toks {
                            col.vals.push(number(c.kind, t).ok_or_else(|| bad(t))?);
                        }
                    }
                    Role::Ignore => {
                        for t in cell_toks {
                            number(c.kind, t).ok_or_else(|| bad(t))?;
                        }
                    }
                }
            }
            atom += 1;
        }
        if lines.any(|l| !strip(l).is_empty()) {
            return Err(format!("frame {frame} has more lines than its atom count"));
        }
    }
    part.extras = extras;
    Ok(part)
}

fn number(kind: u8, t: &[u8]) -> Option<f64> {
    if kind == b'I' {
        atom_int(t).map(f64::from)
    } else {
        atom_float(t)
    }
}

// ── Python ─────────────────────────────────────────────────────────────────

/// The contract number, as `CONTRACT` in the Python package.
#[pyfunction]
fn contract() -> u32 {
    CONTRACT
}

/// Fill the arrays from the frames at bytes `starts[i]..ends[i]` of `path`.
///
/// `numbers`, `positions`, `cell` (or None) and every array of `columns`
/// ({column name: (atoms,) or (atoms, 3) float64}) are this group's rows.
/// `reference`: frame 0's essential columns `[(name, type, count)]`;
/// `extras`: the 1-wide columns a frame may lack, {name: (type, count)}, as
/// earlier groups of the file listed them;
/// `elements`: symbol -> atomic number; `rev`, `excluded`: a 1-wide column is
/// no field when `rev.get(name, name)` is in `excluded`.
///
/// Returns `{"values": {key: (rows, int64 or float64)}, "texts": {key: (rows,
/// [str])}, "new": {column: (atom rows, float64 values)}, "seen": [column],
/// "extras": {name: (type, count)}}`,
/// rows counted within the group. Raises NotFast for anything handed over.
#[pyfunction]
#[pyo3(signature = (path, starts, ends, counts, first_frame, numbers, positions, cell, columns, reference, extras, elements, rev, excluded, threads))]
#[allow(clippy::too_many_arguments)]
fn parse_into<'py>(
    py: Python<'py>,
    path: &str,
    starts: PyReadonlyArray1<u64>,
    ends: PyReadonlyArray1<u64>,
    counts: PyReadonlyArray1<i64>,
    first_frame: i64,
    mut numbers: PyReadwriteArray1<i64>,
    mut positions: PyReadwriteArray2<f64>,
    cell: Option<PyReadwriteArray3<f64>>,
    columns: Vec<(String, PyReadwriteArrayDyn<f64>)>,
    reference: Vec<(String, String, usize)>,
    extras: HashMap<String, (String, usize)>,
    elements: HashMap<String, i64>,
    rev: HashMap<String, String>,
    excluded: HashSet<String>,
    threads: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let file = std::fs::File::open(path)?;
    // SAFETY: the file is only read; FFAST's frame list fails on a file changed since.
    let data = unsafe { Mmap::map(&file)? };
    let starts = starts.as_slice()?;
    let ends = ends.as_slice()?;
    let counts = counts.as_slice()?;
    let n = starts.len();
    if ends.len() != n || counts.len() != n {
        return Err(PyValueError::new_err("starts, ends and counts differ in length"));
    }
    if starts.iter().zip(ends).any(|(&s, &e)| s > e || e as usize > data.len()) {
        return Err(NotFast::new_err("a frame's bytes lie outside the file; it changed"));
    }
    let atoms = counts.iter().map(|&c| c.max(0) as usize).sum::<usize>();
    let elements: HashMap<Vec<u8>, i64> = elements.into_iter().map(|(k, v)| (k.into_bytes(), v)).collect();
    let reference: Vec<(String, u8, usize)> =
        reference.into_iter().map(|(name, kind, n)| (name, kind.bytes().next().unwrap_or(b'?'), n)).collect();
    let known_extras: HashMap<String, (u8, usize)> =
        extras.into_iter().map(|(name, (kind, n))| (name, (kind.bytes().next().unwrap_or(b'?'), n))).collect();

    let mut columns = columns;
    let mut targets = Vec::new();
    let mut col_slices = Vec::new();
    for (name, array) in columns.iter_mut() {
        let width = match array.shape() {
            [rows] if *rows == atoms => 1,
            [rows, w] if *rows == atoms => *w,
            _ => return Err(PyValueError::new_err(format!("column {name}: not one row per atom"))),
        };
        targets.push((name.clone(), width));
        col_slices.push(array.as_slice_mut()?);
    }
    let numbers = numbers.as_slice_mut()?;
    let pos = positions.as_slice_mut()?;
    let mut cell = cell;
    let cell_s = match cell.as_mut() {
        Some(c) => Some(c.as_slice_mut()?),
        None => None,
    };
    if numbers.len() != atoms || pos.len() != atoms * 3 || cell_s.as_ref().is_some_and(|c| c.len() != n * 9) {
        return Err(PyValueError::new_err("the arrays do not fit the frames' atom counts"));
    }

    // One chunk per piece of the frames, each with its own slices.
    let threads = threads.max(1);
    let per = n.div_ceil(threads * 4).max(1);
    let mut chunks = Vec::new();
    let (mut rn, mut rp, mut rc) = (numbers, pos, cell_s);
    let mut rcols = col_slices;
    let (mut a, mut atom0) = (0usize, 0usize);
    while a < n {
        let b = (a + per).min(n);
        let m = counts[a..b].iter().map(|&c| c.max(0) as usize).sum::<usize>();
        let (n0, n1) = std::mem::take(&mut rn).split_at_mut(m);
        let (p0, p1) = std::mem::take(&mut rp).split_at_mut(m * 3);
        let c0 = match rc.take() {
            Some(c) => {
                let (x, y) = c.split_at_mut((b - a) * 9);
                rc = Some(y);
                Some(x)
            }
            None => None,
        };
        let mut cols = Vec::with_capacity(rcols.len());
        for (k, rest) in rcols.iter_mut().enumerate() {
            let (x, y) = std::mem::take(rest).split_at_mut(m * targets[k].1);
            *rest = y;
            cols.push(x);
        }
        rn = n1;
        rp = p1;
        chunks.push(Chunk { a, b, atom0, numbers: n0, pos: p0, cell: c0, cols });
        a = b;
        atom0 += m;
    }

    let ctx = Ctx {
        data: &data,
        starts,
        ends,
        counts,
        first_frame,
        elements: &elements,
        rev: &rev,
        excluded_names: &excluded,
        targets: &targets,
        reference: &reference,
        known_extras: &known_extras,
    };
    let parts: Result<Vec<Part>, String> = py.allow_threads(|| {
        let pool = rayon::ThreadPoolBuilder::new().num_threads(threads).build().map_err(|e| e.to_string())?;
        pool.install(|| chunks.into_par_iter().map(|c| parse_chunk(&ctx, c)).collect())
    });
    let parts = parts.map_err(NotFast::new_err)?;

    // Join the chunks in order.
    let mut values: Keyed<Value> = Keyed::default();
    let mut texts: Keyed<String> = Keyed::default();
    let mut new: Vec<NewCol> = Vec::new();
    let mut seen = vec![false; targets.len()];
    let mut extras = known_extras.clone();
    for part in parts {
        for (name, spec) in part.extras {
            if *extras.entry(name.clone()).or_insert(spec) != spec {
                return Err(NotFast::new_err(format!("column {name} changes its type")));
            }
        }
        values.extend(part.values);
        texts.extend(part.texts);
        for col in part.new {
            match new.iter_mut().find(|c| c.name == col.name) {
                Some(c) if c.width == col.width => {
                    c.rows.extend(col.rows);
                    c.vals.extend(col.vals);
                }
                Some(_) => return Err(NotFast::new_err(format!("column {} changes its width", col.name))),
                None => new.push(col),
            }
        }
        for (s, p) in seen.iter_mut().zip(part.seen) {
            *s |= p;
        }
    }

    let out = PyDict::new_bound(py);
    let vals = PyDict::new_bound(py);
    for (key, rows, xs) in values.keys {
        let rows = PyArray1::from_vec_bound(py, rows);
        let key = String::from_utf8_lossy(&key).into_owned();
        if xs.iter().all(|x| matches!(x, Value::I(_))) {
            let ints: Vec<i64> = xs.iter().map(|x| if let Value::I(i) = x { *i } else { 0 }).collect();
            vals.set_item(key, (rows, PyArray1::from_vec_bound(py, ints)))?;
        } else {
            // As np.array makes a list mixing ints and floats.
            let floats: Vec<f64> = xs.iter().map(|x| match x { Value::I(i) => *i as f64, Value::F(f) => *f }).collect();
            vals.set_item(key, (rows, PyArray1::from_vec_bound(py, floats)))?;
        }
    }
    let txts = PyDict::new_bound(py);
    for (key, rows, xs) in texts.keys {
        txts.set_item(String::from_utf8_lossy(&key).into_owned(), (PyArray1::from_vec_bound(py, rows), PyList::new_bound(py, xs)))?;
    }
    let news = PyDict::new_bound(py);
    for col in new {
        let rows = col.rows.len();
        let vals = PyArray1::from_vec_bound(py, col.vals);
        let vals = if col.width == 1 { vals.into_any() } else { vals.reshape([rows, col.width])?.into_any() };
        news.set_item(col.name, (PyArray1::from_vec_bound(py, col.rows), vals))?;
    }
    let seen: Vec<&str> = targets.iter().zip(&seen).filter(|(_, s)| **s).map(|((name, _), _)| name.as_str()).collect();
    out.set_item("values", vals)?;
    out.set_item("texts", txts)?;
    out.set_item("new", news)?;
    out.set_item("seen", seen)?;
    let found = PyDict::new_bound(py);
    for (name, (kind, n)) in extras {
        found.set_item(name, ((kind as char).to_string(), n))?;
    }
    out.set_item("extras", found)?;
    Ok(out)
}

// ── finding the frames ────────────────────────────────────────────────────

/// `find_frames` stopped at the chunk's end; `next` is the next frame's start.
const FIND_MORE: u8 = 0;
/// The file is done; `next` is where its last frame ends (trailing blank lines
/// are not part of it).
const FIND_END: u8 = 1;
/// A frame the Python scan must read starts at `next`: its count line is not
/// plain digits (a sign, `1_000`, text, a blank line with frames after it),
/// or the file ends inside it. The Python scan reads it and gives the error.
const FIND_PYTHON: u8 = 2;

/// The end of the line starting at `p`, past its `\n` (or the file's end).
fn line_end(data: &[u8], p: usize) -> usize {
    data[p..].iter().position(|&b| b == b'\n').map_or(data.len(), |i| p + i + 1)
}

/// The positions of every `\n` in `block` (which starts at byte `base`),
/// eight bytes at a time: most words of an .xyz file hold no line end.
fn newlines_in(block: &[u8], base: usize) -> Vec<usize> {
    const ONES: u64 = 0x0101_0101_0101_0101;
    const HIGH: u64 = 0x8080_8080_8080_8080;
    const NL: u64 = 0x0a0a_0a0a_0a0a_0a0a;
    let mut out = Vec::with_capacity(block.len() / 48);
    let mut words = block.chunks_exact(8);
    let mut i = 0;
    for w in words.by_ref() {
        let x = u64::from_le_bytes(w.try_into().unwrap()) ^ NL;
        if x.wrapping_sub(ONES) & !x & HIGH != 0 {
            out.extend(w.iter().enumerate().filter(|(_, &b)| b == b'\n').map(|(j, _)| base + i + j));
        }
        i += 8;
    }
    out.extend(words.remainder().iter().enumerate().filter(|(_, &b)| b == b'\n').map(|(j, _)| base + i + j));
    out
}

/// Line ends, found in parallel over `[from, upto)` before the walk; a line
/// running past `upto` is followed byte by byte.
struct Lines<'a> {
    data: &'a [u8],
    nl: Vec<usize>,
    k: usize,
    upto: usize,
}

impl<'a> Lines<'a> {
    fn new(data: &'a [u8], from: usize, upto: usize, threads: usize) -> Self {
        const PIECE: usize = 1 << 22;
        let pieces: Vec<usize> = (from..upto).step_by(PIECE).collect();
        let found = |a: &usize| newlines_in(&data[*a..(*a + PIECE).min(upto)], *a);
        let parts: Vec<Vec<usize>> = match rayon::ThreadPoolBuilder::new().num_threads(threads.max(1)).build() {
            Ok(pool) if threads > 1 && pieces.len() > 1 => pool.install(|| pieces.par_iter().map(found).collect()),
            _ => pieces.iter().map(found).collect(),
        };
        Lines { data, nl: parts.concat(), k: 0, upto }
    }

    /// The end of the line starting at `p`, past its `\n` (or the file's
    /// end). Calls come with increasing `p`.
    fn end(&mut self, p: usize) -> usize {
        if p < self.upto {
            while self.k < self.nl.len() && self.nl[self.k] < p {
                self.k += 1;
            }
            if self.k < self.nl.len() {
                return self.nl[self.k] + 1;
            }
        }
        line_end(self.data, p)
    }
}

fn find_in(data: &[u8], offset: usize, limit: usize, threads: usize) -> (Vec<i64>, Vec<i64>, usize, u8) {
    let (mut starts, mut counts) = (Vec::new(), Vec::new());
    let stop = offset.saturating_add(limit.max(1));
    let mut lines = Lines::new(data, offset, stop.min(data.len()), threads);
    let mut pos = offset;
    loop {
        if pos >= data.len() {
            return (starts, counts, pos, FIND_END);
        }
        if pos >= stop {
            return (starts, counts, pos, FIND_MORE);
        }
        let head = lines.end(pos);
        let text = strip(&data[pos..head]);
        if text.is_empty() {
            // Trailing blank lines end the file; anything after them is an error.
            let state = if data[head..].iter().all(|&b| line_space(b)) { FIND_END } else { FIND_PYTHON };
            return (starts, counts, pos, state);
        }
        if text.len() > 18 || !text.iter().all(u8::is_ascii_digit) {
            return (starts, counts, pos, FIND_PYTHON);
        }
        let natoms = text.iter().fold(0i64, |n, &d| n * 10 + (d - b'0') as i64);
        // The comment line and `natoms` atom lines; each must exist.
        let mut p = head;
        for _ in 0..=natoms {
            if p >= data.len() {
                return (starts, counts, pos, FIND_PYTHON);
            }
            p = lines.end(p);
        }
        starts.push(pos as i64);
        counts.push(natoms);
        pos = p;
    }
}

/// The frames of `path` from byte `offset` (a frame's start), up to the first
/// frame starting at or after `offset + limit`: `(starts, counts, next,
/// state)`, `state` one of FIND_MORE, FIND_END, FIND_PYTHON. The same frames
/// as `frame_index.scan`; anything unusual is left to it (FIND_PYTHON).
/// `threads` look for the line ends.
#[pyfunction]
#[pyo3(signature = (path, offset, limit, threads=1))]
fn find_frames<'py>(
    py: Python<'py>,
    path: &str,
    offset: u64,
    limit: u64,
    threads: usize,
) -> PyResult<(Bound<'py, PyArray1<i64>>, Bound<'py, PyArray1<i64>>, u64, u8)> {
    let file = std::fs::File::open(path)?;
    let size = file.metadata()?.len();
    let (starts, counts, next, state) = if offset >= size {
        (Vec::new(), Vec::new(), offset as usize, FIND_END)
    } else {
        // SAFETY: the file is only read; the frame list is checked against its size and time.
        let data = unsafe { Mmap::map(&file)? };
        py.allow_threads(|| find_in(&data, offset as usize, limit as usize, threads))
    };
    Ok((PyArray1::from_vec_bound(py, starts), PyArray1::from_vec_bound(py, counts), next as u64, state))
}

// ── energies for sampling ─────────────────────────────────────────────────
//
// A copy of `extxyz_arrays.header_energies` (the quick read) and its
// `_quoted_values`, step for step. A frame whose header holds a value only
// Python can judge (`nan`, `1_000`, a 400-digit integer) or a tab or `\r` is
// handed back: Python reads that frame with its own code.

/// `bytes.find(needle, from)`.
fn find_at(h: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if from > h.len() || needle.len() > h.len() - from {
        return None;
    }
    h[from..].windows(needle.len()).position(|w| w == needle).map(|i| from + i)
}

/// `_quoted_values`: (start, end) of each quoted value `="…"` or `='…'`.
fn quoted_values(h: &[u8]) -> Vec<(usize, usize)> {
    let mut spans = Vec::new();
    let mut pos = 0;
    let mut a = find_at(h, b"=\"", 0);
    let mut b = find_at(h, b"='", 0);
    loop {
        if matches!(a, Some(x) if x < pos) {
            a = find_at(h, b"=\"", pos);
        }
        if matches!(b, Some(x) if x < pos) {
            b = find_at(h, b"='", pos);
        }
        let opener = match (a, b) {
            (None, None) => return spans,
            (Some(x), None) | (None, Some(x)) => x,
            (Some(x), Some(y)) => x.min(y),
        };
        let quote = h[opener + 1];
        let end = if h.get(opener + 2..).is_some_and(|t| t.starts_with(b"_JSON ")) {
            // JSON text holds many \" inside; after a ] or } only the closing quote can follow.
            let x = find_at(h, &[b']', quote], opener + 2);
            let y = find_at(h, &[b'}', quote], opener + 2);
            match (x, y) {
                (None, None) => None,
                (Some(e), None) | (None, Some(e)) => Some(e + 1),
                (Some(e), Some(f)) => Some(e.min(f) + 1),
            }
        } else {
            let mut end = find_at(h, &[quote], opener + 2);
            while let Some(e) = end {
                if e == 0 || h[e - 1] != b'\\' {
                    break;
                }
                let (mut slashes, mut k) = (0, e - 1);
                while k > opener && h[k] == b'\\' {
                    slashes += 1;
                    k -= 1;
                }
                if slashes % 2 == 0 {
                    break;
                }
                end = find_at(h, &[quote], e + 1);
            }
            end
        };
        let Some(end) = end else { return spans };
        spans.push((opener + 1, end + 1));
        pos = end + 1;
    }
}

/// What a header value gives, as `header_energies` reads it.
enum EnergyValue {
    Set(f64),
    Skip,
    Python,
}

fn energy_value(v: &[u8]) -> EnergyValue {
    if v.is_empty() || v.starts_with(b"_JSON ") {
        return EnergyValue::Skip;
    }
    if int_text(v) {
        // float(int(v)): a zero has no sign; beyond 300 digits Python raises.
        if v.len() > 300 {
            return EnergyValue::Python;
        }
        return match as_str(v).parse::<f64>() {
            Ok(x) => EnergyValue::Set(if x == 0.0 { 0.0 } else { x }),
            Err(_) => EnergyValue::Python,
        };
    }
    if float_text(v) {
        return match as_str(v).parse::<f64>() {
            Ok(x) => EnergyValue::Set(x),
            Err(_) => EnergyValue::Python,
        };
    }
    // Only these bytes can make text Python's float() reads (`nan`, `inf`,
    // `infinity`, `1_0`, ` 5`); text with any other byte is no number.
    let maybe = |b: &u8| b.is_ascii_digit() || b"+-._eEiInNfFtTyYaA".contains(b) || *b <= b' ';
    if v.iter().all(maybe) {
        EnergyValue::Python
    } else {
        EnergyValue::Skip
    }
}

fn energy_key(k: &[u8]) -> bool {
    !k.is_empty() && (k[0].is_ascii_alphabetic() || k[0] == b'_') && k.iter().all(|&b| word(b))
}

/// `header_energies(h)` as `(key, value)` in the order Python's dict holds
/// them, or None when Python must read this header.
fn header_energies(h: &[u8], per_config: &HashSet<Vec<u8>>) -> Option<Vec<(Vec<u8>, f64)>> {
    let low = h.to_ascii_lowercase();
    let mut out: Vec<(Vec<u8>, f64)> = Vec::new();
    let Some(mut at) = find_at(&low, b"energy", 0) else { return Some(out) };
    if !h.contains(&b'=') {
        return Some(out);
    }
    if h.contains(&b'\t') || h.contains(&b'\r') {
        return None;
    }
    let spans = quoted_values(h);
    loop {
        let mut nxt = at + 6;
        let key_start = h[..at].iter().rposition(|&b| b == b' ').map_or(0, |p| p + 1);
        let eq = find_at(h, b"=", at);
        let space = find_at(h, b" ", at);
        let inside = spans.iter().any(|&(a, b)| a < key_start && key_start < b);
        if let Some(eq) = eq {
            if space.is_none_or(|s| eq < s) && !inside {
                let key = &h[key_start..eq];
                if energy_key(key) && !(key != b"energy" && per_config.contains(key)) {
                    let next = h.get(eq + 1).copied();
                    let value: Option<&[u8]> = if next == Some(b'"') || next == Some(b'\'') {
                        let q = next.unwrap();
                        let raw = match find_at(h, &[q], eq + 2) {
                            Some(close) => &h[eq + 2..close],
                            None => &h[0..0],
                        };
                        if raw.contains(&b'\\') { None } else { Some(raw) }
                    } else {
                        let end = find_at(h, b" ", eq + 1).unwrap_or(h.len());
                        let raw = &h[eq + 1..end];
                        if raw.first().is_some_and(|&c| c == b'{' || c == b'[') {
                            None
                        } else {
                            let keep = raw.iter().rposition(|&b| !line_space(b)).map_or(0, |p| p + 1);
                            Some(&raw[..keep])
                        }
                    };
                    if let Some(v) = value {
                        match energy_value(v) {
                            EnergyValue::Set(x) => match out.iter_mut().find(|(k, _)| k == key) {
                                Some(slot) => slot.1 = x,
                                None => out.push((key.to_vec(), x)),
                            },
                            EnergyValue::Skip => {}
                            EnergyValue::Python => return None,
                        }
                    }
                }
                nxt = nxt.max(eq);
            }
        }
        match find_at(&low, b"energy", nxt) {
            Some(x) => at = x,
            None => return Some(out),
        }
    }
}

/// One frame's energies, or None for Python: its header is the line after
/// the one at `start`, as `XyzReader.energies` takes it.
fn frame_energies(data: &[u8], start: usize, per_config: &HashSet<Vec<u8>>) -> Option<Vec<(Vec<u8>, f64)>> {
    let nl = data.get(start..)?.iter().position(|&b| b == b'\n')? + start;
    let head = nl + 1;
    let end = data[head..].iter().position(|&b| b == b'\n').map_or(data.len(), |i| head + i);
    header_energies(&data[head..end], per_config)
}

/// Energies of a run of frames: per name, its first (frame, place in that
/// frame's header) and its values; and the frames Python must read.
#[derive(Default)]
struct EnergyPart {
    names: Vec<Vec<u8>>,
    first: Vec<(usize, usize)>,
    values: Vec<Vec<(usize, f64)>>,
    python: Vec<i64>,
}

fn energies_of(data: &[u8], starts: &[u64], from: usize, per_config: &HashSet<Vec<u8>>) -> EnergyPart {
    let mut part = EnergyPart::default();
    let mut index: HashMap<Vec<u8>, usize> = HashMap::new();
    for (j, &s) in starts.iter().enumerate() {
        let frame = from + j;
        let Some(items) = frame_energies(data, s as usize, per_config) else {
            part.python.push(frame as i64);
            continue;
        };
        for (rank, (key, value)) in items.into_iter().enumerate() {
            let k = *index.entry(key.clone()).or_insert_with(|| {
                part.names.push(key);
                part.first.push((frame, rank));
                part.values.push(Vec::new());
                part.names.len() - 1
            });
            part.values[k].push((frame, value));
        }
    }
    part
}

/// `XyzReader.energies(path, starts)` on `threads` threads: `(names, firsts,
/// columns, python)`. `names` in the order they first appear, `firsts[i]` =
/// (frame, place in its header) where name i first appears, `columns[i]`
/// one float per frame (NaN where it has none). `python`: the frames whose
/// headers Python must read (their values here are NaN). `per_config`: ASE's
/// per-frame property names (not energies, except `energy`).
#[pyfunction]
#[pyo3(signature = (path, starts, per_config, threads=1))]
#[allow(clippy::type_complexity)]
fn energies<'py>(
    py: Python<'py>,
    path: &str,
    starts: PyReadonlyArray1<u64>,
    per_config: Vec<String>,
    threads: usize,
) -> PyResult<(Vec<String>, Vec<(usize, usize)>, Vec<Bound<'py, PyArray1<f64>>>, Bound<'py, PyArray1<i64>>)> {
    let starts = starts.as_slice()?;
    let n = starts.len();
    let file = std::fs::File::open(path)?;
    let empty = file.metadata()?.len() == 0;
    let per_config: HashSet<Vec<u8>> = per_config.into_iter().map(String::into_bytes).collect();
    let mapped = if empty || n == 0 { None } else { Some(unsafe { Mmap::map(&file)? }) };
    let data: &[u8] = mapped.as_deref().unwrap_or(&[]);
    let (names, first, columns, python) = py.allow_threads(|| {
        let threads = threads.max(1);
        let per = n.div_ceil(threads * 4).max(1);
        let pieces: Vec<usize> = (0..n).step_by(per).collect();
        let one = |a: &usize| energies_of(data, &starts[*a..(*a + per).min(n)], *a, &per_config);
        let parts: Vec<EnergyPart> = match rayon::ThreadPoolBuilder::new().num_threads(threads).build() {
            Ok(pool) if threads > 1 && pieces.len() > 1 => pool.install(|| pieces.par_iter().map(one).collect()),
            _ => pieces.iter().map(one).collect(),
        };
        // Parts come in frame order, so a name's first part holds its first frame.
        let mut index: HashMap<Vec<u8>, usize> = HashMap::new();
        let (mut names, mut first, mut columns, mut python) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
        for part in parts {
            for ((name, f), values) in part.names.into_iter().zip(part.first).zip(part.values) {
                let k = *index.entry(name.clone()).or_insert_with(|| {
                    names.push(String::from_utf8_lossy(&name).into_owned());
                    first.push(f);
                    columns.push(vec![f64::NAN; n]);
                    names.len() - 1
                });
                for (frame, v) in values {
                    columns[k][frame] = v;
                }
            }
            python.extend(part.python);
        }
        (names, first, columns, python)
    });
    let columns = columns.into_iter().map(|c| PyArray1::from_vec_bound(py, c)).collect();
    Ok((names, first, columns, PyArray1::from_vec_bound(py, python)))
}

#[pymodule]
fn _ffast_xyz(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(contract, m)?)?;
    m.add_function(wrap_pyfunction!(parse_into, m)?)?;
    m.add_function(wrap_pyfunction!(find_frames, m)?)?;
    m.add_function(wrap_pyfunction!(energies, m)?)?;
    m.add("FIND_MORE", FIND_MORE)?;
    m.add("FIND_END", FIND_END)?;
    m.add("FIND_PYTHON", FIND_PYTHON)?;
    m.add("NotFast", m.py().get_type_bound::<NotFast>())?;
    Ok(())
}
