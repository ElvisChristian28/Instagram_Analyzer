"""Diagnose why engagement matrix only shows 2 entries."""
import os, glob, json

meta_files = glob.glob('scraped_data/metadata/*.json')
img_files  = glob.glob('scraped_data/media/*.jpg') + glob.glob('scraped_data/media/*.mp4')
idx_files  = glob.glob('scraped_data/*_index.json')
csv_files  = glob.glob('scraped_data/reports/*.csv')

print(f"Metadata files : {len(meta_files)}")
print(f"Media files    : {len(img_files)}")
print(f"Index files    : {len(idx_files)}")
print(f"CSV files      : {len(csv_files)}")
print()

for idx in idx_files:
    with open(idx) as f:
        d = json.load(f)
    scs = d.get('shortcodes', [])
    print(f"Index: {os.path.basename(idx)}")
    print(f"  hashtag    = {d.get('hashtag')}")
    print(f"  shortcodes = {len(scs)}")

print()
print("First 5 metadata filenames:")
for fp in sorted(meta_files)[:5]:
    print(f"  {os.path.basename(fp)}")
    with open(fp) as f:
        d = json.load(f)
    print(f"    shortcode={d.get('shortcode')} likes={d.get('like_count')} owner={d.get('owner_username')}")

print()
# Check how many metadata shortcodes match any index shortcode
all_idx_sc = set()
for idx in idx_files:
    with open(idx) as f:
        d = json.load(f)
    all_idx_sc.update(d.get('shortcodes', []))

meta_sc = set()
for fp in meta_files:
    with open(fp) as f:
        d = json.load(f)
    sc = d.get('shortcode') or os.path.basename(fp).replace('_metadata.json','')
    meta_sc.add(sc)

print(f"Shortcodes in index files  : {len(all_idx_sc)}")
print(f"Shortcodes in metadata dir : {len(meta_sc)}")
print(f"Overlap (matched)          : {len(all_idx_sc & meta_sc)}")
print(f"Metadata WITHOUT index entry: {len(meta_sc - all_idx_sc)}")
if meta_sc - all_idx_sc:
    print("  Examples:", list(meta_sc - all_idx_sc)[:5])
