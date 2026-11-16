import struct,sys
from PIL import Image
def packets(fn):
    d=open(fn,'rb').read(); o=0
    while o<len(d):
        t,l=struct.unpack_from('<II',d,o)
        if t==6:  # EPB
            cl=struct.unpack_from('<I',d,o+20)[0]
            yield d[o+28:o+28+cl]
        o+=l
W,H=1016,592
n=0
for p in packets(sys.argv[1]):
    typ=chr(p[8]); xfer=p[9]; ep=p[10]; dev=p[11]
    if typ=='S' and xfer==3 and ep==1:
        pay=p[64:]
        print(n,dev,len(pay),pay[:8].hex())
        rows=len(pay)//(W*2)
        im=Image.new("RGB",(W,rows)); px=im.load()
        for y in range(rows):
            for x in range(W):
                v=pay[(y*W+x)*2]|(pay[(y*W+x)*2+1]<<8)
                px[x,y]=(((v>>11)&31)*255//31,((v>>5)&63)*255//63,(v&31)*255//31)
        im.save(f"/tmp/wigicap/frame{n}.png"); n+=1
