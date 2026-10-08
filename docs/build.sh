#!/bin/sh
# Wrap the page fragment in a full HTML document so docs/index.html opens on its own.
cd "$(dirname "$0")" || exit 1
{
    printf '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<style>body{margin:0}[hidden]{display:none!important}</style>\n</head>\n<body>\n'
    cat stackmap.fragment.html
    printf '</body>\n</html>\n'
} > index.html
