from pathlib import Path
import argparse, base64, zlib, gzip, bz2, lzma, re, sys, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
BLOB_RE = re.compile(rb"(?:b|B)['\"]([A-Za-z0-9+/=\-_]{60,})['\"]")
EXEC_BLOB_RE = re.compile(rb"exec\([^\)]*b['\"]([A-Za-z0-9+/=\-_]{60,})['\"][^\)]*\)")
def try_decompress(decoded: bytes):
    for wbits, name in ((15, "zlib(wbits=15)"), (-15, "raw_deflate(wbits=-15)"), (31, "zlib/gzip(wbits=31)")):
        try:
            out = zlib.decompress(decoded, wbits)
            return out, name
        except Exception:
            pass

    try:
        out = gzip.decompress(decoded)
        return out, "gzip"
    except Exception:
        pass

    try:
        out = bz2.decompress(decoded)
        return out, "bz2"
    except Exception:
        pass

    try:
        out = lzma.decompress(decoded)
        return out, "lzma"
    except Exception:
        pass

    return decoded, "raw"

def decode_blob_string(blob_bytes: bytes):
    rev = blob_bytes[::-1]

    decoded = base64.b64decode(rev, validate=False)
    out_bytes, method = try_decompress(decoded)
    return out_bytes, method

def recursive_decode_bytes(initial_bytes: bytes, out_dir: Path, prefix: str, save_stages: bool=True, max_stage: int=50):
    cur = initial_bytes
    stages = []
    stage = 1
    while stage <= max_stage:
        m = EXEC_BLOB_RE.search(cur) or BLOB_RE.search(cur)
        if not m:
            break
        blob = m.group(1)
        try:
            out_bytes, method = decode_blob_string(blob)
        except Exception as e:
            print(f"[!] Stage {stage} decode error: {e}")
            break

        if save_stages:
            try:
                txt = out_bytes.decode('utf-8', errors='strict')
                stage_path = out_dir / f"{prefix}_stage{stage}.py"
                stage_path.write_text(txt, encoding='utf-8', errors='replace')
            except Exception:
                stage_path = out_dir / f"{prefix}_stage{stage}.bin"
                stage_path.write_bytes(out_bytes)
            stages.append((stage_path, method))
            print(f"[+] Saved stage {stage} -> {stage_path} (method: {method})")
        cur = out_bytes
        stage += 1

    try:
        final_text = cur.decode('utf-8', errors='replace')
        final_path = out_dir / f"{prefix}_final.py"
        final_path.write_text(final_text, encoding='utf-8', errors='replace')
        print(f"[+] Final saved as text: {final_path}")
    except Exception:
        final_path = out_dir / f"{prefix}_final.bin"
        final_path.write_bytes(cur)
        print(f"[+] Final saved as binary: {final_path}")

    return final_path, stages

def extract_blobs_from_file_bytes(content_bytes: bytes):
    results = []
    for m in BLOB_RE.finditer(content_bytes):
        results.append((m.start(), m.group(1)))
    return results

def process_single_file(path: Path, out_dir: Path, idx_base=0, save_stages=True, max_stage=50):
    outputs = []
    try:
        raw = path.read_bytes()
    except Exception as e:
        print(f"[!] Failed to read {path}: {e}")
        return outputs

    blobs = extract_blobs_from_file_bytes(raw)
    if not blobs:
        print(f"[i] No blob found in {path}")
        return outputs

    for i, (pos, blob) in enumerate(blobs, start=1):
        prefix = f"{path.stem}_blob{i + idx_base}"
        try:
            out_bytes, method = decode_blob_string(blob)
            if save_stages:
                try:
                    txt = out_bytes.decode('utf-8', errors='strict')
                    p = out_dir / f"{prefix}_stage0.py"
                    p.write_text(txt, encoding='utf-8', errors='replace')
                except Exception:
                    p = out_dir / f"{prefix}_stage0.bin"
                    p.write_bytes(out_bytes)
                print(f"[+] Saved top decode {p} (method {method})")

            final_path, stage_list = recursive_decode_bytes(out_bytes, out_dir, prefix, save_stages, max_stage)
            outputs.append((final_path, True, method, stage_list))
        except Exception as e:
            print(f"[!] Failed decode blob {i} in {path}: {e}")
    return outputs

def gather_input_files(p: Path):
    if p.is_file():
        return [p]
    elif p.is_dir():
        return [x for x in p.iterdir() if x.is_file()]
    else:
        raise FileNotFoundError(str(p))

def main():
    parser = argparse.ArgumentParser(description="Multi-file recursive blob decryptor")
    parser.add_argument("-i","--input", required=True, help="Input file or directory")
    parser.add_argument("-o","--outdir", default="decrypted_out", help="Output directory")
    parser.add_argument("-w","--workers", type=int, default=4, help="Parallel workers")
    parser.add_argument("--start-index", type=int, default=0, help="Index offset for naming")
    parser.add_argument("--max-stage", type=int, default=50, help="Max recursion stages")
    parser.add_argument("--no-stages", action="store_true", help="Do not save intermediate stage files")
    args = parser.parse_args()

    inp = Path(args.input)
    out_dir = Path(args.outdir); out_dir.mkdir(parents=True, exist_ok=True)
    files = gather_input_files(inp)
    if not files:
        print("[i] No files found")
        return

    lock = threading.Lock()
    all_outputs = []
    with ThreadPoolExecutor(max_workers=args.workers) as exe:
        futures = { exe.submit(process_single_file, f, out_dir, args.start_index + idx*1000, not args.no_stages, args.max_stage): f for idx,f in enumerate(files) }
        for fut in as_completed(futures):
            f = futures[fut]
            try:
                res = fut.result()
                with lock:
                    all_outputs.extend(res)
            except Exception as e:
                print(f"[!] Error processing {f}: {e}")

    print("\n=== Done ===")
    for out, ok, method, stages in all_outputs:
        print(f" - {out} (ok={ok}, initial_method={method}, stages_saved={len(stages)})")

if __name__ == "__main__":
    main()