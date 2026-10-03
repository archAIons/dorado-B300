from collections import Counter
import json
import math


def filter_and_summarize(folder, options):
    import pysam

    original = folder / "calls.bam"
    filtered = folder / "filtered.bam"
    counts = Counter()
    lengths = Counter()
    quality_bins = Counter()
    barcodes = Counter()
    sum_quality = 0.0
    with pysam.AlignmentFile(str(original), "rb", check_sq=False) as source:
        with pysam.AlignmentFile(str(filtered), "wb", template=source) as target:
            for read in source.fetch(until_eof=True):
                length = read.query_length or 0
                if read.has_tag("qs"):
                    qscore = float(read.get_tag("qs"))
                elif read.query_qualities:
                    qscore = -10 * math.log10(
                        sum(10 ** (-q / 10) for q in read.query_qualities)
                        / len(read.query_qualities)
                    )
                else:
                    qscore = 0
                counts["total_reads"] += 1
                counts["total_bases"] += length
                lengths[length] += 1
                quality_bins[int(qscore)] += 1
                sum_quality += qscore
                barcodes[
                    str(read.get_tag("BC")) if read.has_tag("BC") else "unclassified"
                ] += 1
                if qscore < options.min_qscore or length < options.min_length:
                    counts["filtered_reads"] += 1
                    continue
                counts["passed_reads"] += 1
                counts["passed_bases"] += length
                target.write(read)
    filtered.replace(original)
    report = {
        k: counts[k]
        for k in (
            "total_reads",
            "total_bases",
            "passed_reads",
            "passed_bases",
            "filtered_reads",
        )
    }
    cumulative = 0
    n50 = 0
    for length in sorted(lengths, reverse=True):
        cumulative += length * lengths[length]
        if cumulative >= counts["total_bases"] / 2:
            n50 = length
            break
    report.update(
        mean_read_length=counts["total_bases"] / max(1, counts["total_reads"]),
        mean_read_qscore=sum_quality / max(1, counts["total_reads"]),
        read_n50=n50,
        quality_histogram=dict(quality_bins),
        barcode_counts=dict(barcodes),
        settings=options.model_dump(),
        summary_scope="All called reads before filtering; BAM/FASTQ contain passed reads.",
    )
    if options.qc:
        (folder / "qc.json").write_text(json.dumps(report, indent=2))
    return report
