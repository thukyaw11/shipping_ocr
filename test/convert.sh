#!/bin/bash

# Usage: ./convert.sh input.pdf page_number output.png
INPUT_PDF=$1
PAGE_NUM=$2
OUTPUT_IMG=$3

# Note: ImageMagick uses 0-based indexing. 
# We subtract 1 so the user can input "1" for the first page.
INDEX=$((PAGE_NUM - 1))

magick -density 300 "${INPUT_PDF}[$INDEX]" -quality 100 "$OUTPUT_IMG"

echo "Page $PAGE_NUM converted to $OUTPUT_IMG"
