# $remote_addr, deliberately, and not $proxy_add_x_forwarded_for.
#
# The append form keeps whatever the client sent and adds the peer after it.
# app/services/client.py reads the LEFTMOST entry as the original visitor, so
# a visitor who sends their own X-Forwarded-For would choose their own
# identity and could mint a fresh one per request -- inflating the numbers
# this product exists to report. This is the outermost proxy, so the address
# it is talking to is the truth, and anything the client claimed is discarded.
#
# Behind a CDN this is wrong: there the CDN is outermost, and this should
# forward the header the CDN sets instead.
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
