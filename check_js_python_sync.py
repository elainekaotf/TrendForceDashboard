"""
Consistency check between cluster_topics.py's noise-word lists and
generate_dashboard.py's hand-copied JS equivalents (EN_STOPWORDS,
EN_COMMON_WORDS, ZH_COMMON_WORDS).

There's no shared module between the Python pipeline and the embedded page's
JS - the browser can't import Python - so every one of these lists was
pasted in by hand at some point, and nothing stops them silently drifting
apart the next time either side changes. Found the hard way this session:
several rounds of "there's still some words like X leaking through" that
each needed separate discovery, and a real bug (為 mapping to the wrong
character) that survived until someone happened to test that specific
term. This script re-derives what each JS list SHOULD contain from the
Python-side source of truth and fails loudly on any mismatch instead.

Usage: python3 check_js_python_sync.py
Exit code 0 = everything in sync, 1 = drift found (printed explicitly, so
fixing it is a copy-paste from the message, not an investigation).
"""
import re
import sys

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
from wordfreq import top_n_list, zipf_frequency

from cluster_topics import EN_NOISE_WORDS, COMMON_ENGLISH_ZIPF_THRESHOLD, COMMON_CHINESE_ZIPF_THRESHOLD

GENERATE_DASHBOARD = 'generate_dashboard.py'
CJK_WORD_RE = re.compile(r'^[一-鿿]{2,4}$')


def extract_js_set(source, var_name):
    """Pulls every single-quoted string literal out of
    `const <var_name> = new Set([...]);` - a plain text extraction, not a
    JS parser, but these are simple literal string arrays with no
    interpolation, so this is exact."""
    m = re.search(rf"const {var_name} = new Set\(\[(.*?)\]\);", source, re.S)
    if not m:
        raise ValueError(f"Could not find `const {var_name} = new Set([...]);` in {GENERATE_DASHBOARD}")
    return set(re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1)))


def regenerate_common_words(lang, threshold):
    """Reproduces is_common_english_word()/is_common_chinese_word()'s own
    filter (cluster_topics.py) against wordfreq's CURRENT data, so a
    wordfreq package upgrade that changes its frequency list is caught too,
    not just manual edits to the embedded JS copy."""
    words = top_n_list(lang, 20000)
    if lang == 'en':
        return {w for w in words if w.isalpha() and zipf_frequency(w, lang) >= threshold}
    return {w for w in words if CJK_WORD_RE.match(w) and zipf_frequency(w, lang) >= threshold}


def describe_diff(expected, actual, limit=20):
    added = sorted(expected - actual)
    removed = sorted(actual - expected)
    parts = []
    if added:
        shown = added[:limit]
        parts.append(f"{len(added)} missing ({shown}{'...' if len(added) > limit else ''})")
    if removed:
        shown = removed[:limit]
        parts.append(f"{len(removed)} stale/extra ({shown}{'...' if len(removed) > limit else ''})")
    return '; '.join(parts)


def main():
    with open(GENERATE_DASHBOARD, encoding='utf-8') as f:
        source = f.read()

    problems = []

    # EN_STOPWORDS is supposed to embed sklearn's own list verbatim, plus
    # cover every EN_NOISE_WORDS entry (see that variable's own comment in
    # generate_dashboard.py) - not necessarily identical to either set (it
    # also has geography/business words with no server-side equivalent),
    # so check "is a superset of" rather than "equals".
    en_stopwords = extract_js_set(source, 'EN_STOPWORDS')
    missing_sklearn = ENGLISH_STOP_WORDS - en_stopwords
    if missing_sklearn:
        problems.append(
            f"EN_STOPWORDS is missing {len(missing_sklearn)} word(s) from sklearn's current "
            f"ENGLISH_STOP_WORDS (a sklearn upgrade may have changed its list): {sorted(missing_sklearn)}"
        )
    missing_noise = EN_NOISE_WORDS - en_stopwords
    if missing_noise:
        problems.append(
            f"EN_STOPWORDS is missing {len(missing_noise)} word(s) from cluster_topics.py's "
            f"EN_NOISE_WORDS: {sorted(missing_noise)}"
        )

    # EN_COMMON_WORDS / ZH_COMMON_WORDS are meant to be an exact snapshot of
    # is_common_english_word()/is_common_chinese_word()'s own filter applied
    # to wordfreq's data - these should match exactly, not just be supersets.
    expected_en_common = regenerate_common_words('en', COMMON_ENGLISH_ZIPF_THRESHOLD)
    actual_en_common = extract_js_set(source, 'EN_COMMON_WORDS')
    if expected_en_common != actual_en_common:
        problems.append(
            f"EN_COMMON_WORDS has drifted from wordfreq's current English list "
            f"(zipf >= {COMMON_ENGLISH_ZIPF_THRESHOLD}): {describe_diff(expected_en_common, actual_en_common)}"
        )

    expected_zh_common = regenerate_common_words('zh', COMMON_CHINESE_ZIPF_THRESHOLD)
    actual_zh_common = extract_js_set(source, 'ZH_COMMON_WORDS')
    if expected_zh_common != actual_zh_common:
        problems.append(
            f"ZH_COMMON_WORDS has drifted from wordfreq's current Chinese list "
            f"(zipf >= {COMMON_CHINESE_ZIPF_THRESHOLD}): {describe_diff(expected_zh_common, actual_zh_common)}"
        )

    if problems:
        print("JS/Python sync check FAILED:\n")
        for p in problems:
            print(f"- {p}\n")
        sys.exit(1)

    print("JS/Python sync check passed - EN_STOPWORDS, EN_COMMON_WORDS, and ZH_COMMON_WORDS "
          "all match their Python-side source of truth.")


if __name__ == '__main__':
    main()
