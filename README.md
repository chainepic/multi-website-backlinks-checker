<a id="readme-top"></a>

<div align="center">
  <p><strong>English</strong> · <a href="README.zh-CN.md">中文</a></p>

  <img src="docs/logo.png" alt="" width="72" height="72">

  <h1>Multi-website backlinks checker</h1>

  <p>Links between the sites on your list.</p>

  <p>
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+"></a>
    <img src="https://img.shields.io/badge/dependencies-none-2ea44f?style=flat-square" alt="No third-party dependencies">
    <a href="https://github.com/chainepic/multi-website-backlinks-checker/stargazers"><img src="https://img.shields.io/github/stars/chainepic/multi-website-backlinks-checker?style=flat-square" alt="Stars"></a>
  </p>

  <p>Author <a href="https://ro.cafe">Roland · ro.cafe</a></p>

  <p>
    <a href="#interface">Interface</a>
    ·
    <a href="#start">Start</a>
    ·
    <a href="#site-list">Site list</a>
    ·
    <a href="#cells">Cells</a>
    ·
    <a href="#what-gets-fetched">What gets fetched</a>
  </p>
</div>

## Interface

The screenshots use `*.example` domains. They are not real sites.

<p align="center">
  <img src="docs/preview.png" alt="Matrix and one-way list. Every domain is example." width="100%">
</p>

<p align="center">Select a cell and the anchor text and page appear on the right.</p>

<p align="center">
  <img src="docs/detail.png" alt="Link evidence for oak.example to ash.example. Every domain is example." width="100%">
</p>

<p align="right"><a href="#readme-top">Back to top</a></p>

## Start

Python 3.10 or newer. No third-party dependencies.

```bash
git clone https://github.com/chainepic/multi-website-backlinks-checker.git
cd multi-website-backlinks-checker
cp sites.example.json sites.json
python3 server.py
```

Open http://127.0.0.1:8787 . The page opens in English. The button next to Check again switches it to 中文.

Open Sites, paste URLs one per line, then choose Add sites. Remove removes one site. Check again rebuilds the matrix from the new list.

You can also edit `sites.json` directly.

<p align="right"><a href="#readme-top">Back to top</a></p>

## Site list

`sites.json` groups hosts. `host` has no scheme and no `www`.

```json
{
  "max_pages_per_site": 18,
  "groups": [
    {
      "id": "shop",
      "label": "Shop",
      "sites": [
        { "host": "alpha.example" },
        { "host": "beta.example", "note": "optional note" }
      ]
    }
  ]
}
```

`max_pages_per_site` is the page cap for each site. Results are written to `data/report.json`.

`sites.json` and `data/report.json` are in `.gitignore`. They are not committed.

<p align="right"><a href="#readme-top">Back to top</a></p>

## Cells

Rows are the source. Columns are the target.

| Cell | Meaning |
| --- | --- |
| Green, ↔ | Reciprocal. Both sides link |
| Orange, → | One-way. The target is up and does not link back |
| Blue, ! | Target is down. This side links, so a return link cannot be confirmed |
| Hatch, — | Outbound unknown. The source site is down |
| Empty | No link in the fetched pages |
| Red bar | Every link on that edge is nofollow |

<p align="right"><a href="#readme-top">Back to top</a></p>

## What gets fetched

Each site starts at the homepage, then about, product, links, and partner pages, plus URLs whose path mentions another site on the list.

Only `a` tags count. Images, stylesheets, and domains written in the text do not.

JavaScript is not executed. Backlinks outside the list are not checked.

## Tests

```bash
python3 -m unittest
```

The tests do not use the network.

## Author

[Roland](https://ro.cafe)

<p align="right"><a href="#readme-top">Back to top</a></p>
