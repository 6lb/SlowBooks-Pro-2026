# hledger chart fixtures

`riverbend.journal` is a small sign shop's books, written for hledger. The three
exports beside it were produced by **hledger 1.30.1** from that journal, not by
hand, so the importer is tested against what the tool actually writes:

```sh
hledger -f riverbend.journal accounts          > accounts.txt
hledger -f riverbend.journal accounts --types  > accounts-types.txt
hledger -f riverbend.journal balance -O csv --flat > balance.csv
```

Regenerate the same way after editing the journal. Issue #139 / #161 (tresero).
