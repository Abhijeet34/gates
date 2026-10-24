use diff3_sync::merge3;
use tinyhash::digest;

fn main() {
    let base = std::fs::read_to_string("base.md").unwrap_or_default();
    let ours = std::fs::read_to_string("ours.md").unwrap_or_default();
    let theirs = std::fs::read_to_string("theirs.md").unwrap_or_default();
    let merged = merge3(&base, &ours, &theirs);
    println!("{:016x}", digest(merged.as_bytes()));
}
