1) Also worth knowing for later: route writes attributes onto the graph, so two requests running at once would tread on each other. Fine now, a real problem when it's behind the API.

2) route mutates the shared graph. Two simultaneous requests would corrupt each other's weights. A comment on the function is enough for now.
3) The README's "shade_preference": 0.7 doesn't match reality — below α≈3 nothing changes. Update the example to what you actually default to.
4) The "2.3× more shade" phrasing can't work when the baseline has zero shade, which is the common case at midday. Percentage points instead.

5) Use computer vision to find the height of the buildings from the imsages. Use computer vision to find if the new buildings have appeared. Use computer vision to have trees on the map.