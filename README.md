# 多网站互链检查器

Multi-website backlinks checker

一份网站名单，看谁链了谁。只统计名单内部的链接。

作者 [Roland](https://ro.cafe)

## 能看出来的事

| 格子 | 含义 |
| --- | --- |
| 绿，↔ | 互链。两边都有链接 |
| 橙，→ | 单向。对方能打开，但没有链回来 |
| 蓝，! | 对方打不开。这边有链接，回链无法确认 |
| 斜线，— | 出链未知。来源站打不开 |
| 空格 | 抓到的页面里没有这条链接 |
| 底部红线 | 这条边上的链接全是 nofollow |

行是来源，列是目标。点一格可以看到锚文本和页面。

## 运行

需要 Python 3.10 或更新版本。没有第三方依赖。

```bash
cp sites.example.json sites.json
python3 server.py
```

打开 http://127.0.0.1:8787 ，点「重新检查」。

`sites.json` 按分组写域名：

```json
{
  "max_pages_per_site": 18,
  "groups": [
    {
      "id": "shop",
      "label": "商店",
      "sites": [
        { "host": "alpha.example" },
        { "host": "beta.example", "note": "可选备注" }
      ]
    }
  ]
}
```

`host` 不带协议，不带 `www`。`max_pages_per_site` 是每个站最多抓多少页。

检查结果在 `data/report.json`。`sites.json` 和这份结果都在 `.gitignore` 里，不会进仓库。

## 它抓什么

每个站先抓首页，再抓关于、产品、友链、合作这类页面，以及网址路径里出现了其他站名的页面。链接只认页面里的 `a` 标签。图片、样式、正文里写出来的域名不算。

不执行 JavaScript。不查名单以外的 backlink。

## 测试

```bash
python3 -m unittest
```

测试不访问网络。
