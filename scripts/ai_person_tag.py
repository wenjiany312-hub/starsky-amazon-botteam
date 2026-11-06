#!/usr/bin/env python3
"""Merge an AI performer XMP subject after all image transformations.

Adapted from R03 ai_person_tag.py. Preserve unrelated XMP/EXIF/chunks, validate
containers, write atomically and read the actual RDF subject rather than bytes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
import zlib

KEYWORD='contains-synthetic-performer'
PNG=b'\x89PNG\r\n\x1a\n'
XMP_KEY=b'XML:com.adobe.xmp'
JPEG_XMP=b'http://ns.adobe.com/xap/1.0/\x00'
RDF='http://www.w3.org/1999/02/22-rdf-syntax-ns#'
DC='http://purl.org/dc/elements/1.1/'
X='adobe:ns:meta/'
LIMIT=1_000_000
for prefix,namespace in [('x',X),('rdf',RDF),('dc',DC)]:ET.register_namespace(prefix,namespace)


def _png_chunks(data):
    if not data.startswith(PNG):raise ValueError('不是 PNG')
    position=8;ended=False
    while position<len(data):
        if position+12>len(data):raise ValueError('PNG 分块截断')
        size=struct.unpack('>I',data[position:position+4])[0];end=position+12+size
        if end>len(data):raise ValueError('PNG 分块长度无效')
        kind=data[position+4:position+8];body=data[position+8:end-4]
        expected=struct.unpack('>I',data[end-4:end])[0]
        if zlib.crc32(kind+body)&0xffffffff!=expected:raise ValueError('PNG CRC 不匹配')
        yield kind,body,data[position:end]
        position=end
        if kind==b'IEND':ended=True;break
    if not ended or position!=len(data):raise ValueError('PNG 缺少结尾或存在异常尾部')


def _jpeg_segments(data):
    if not data.startswith(b'\xff\xd8'):raise ValueError('不是 JPEG')
    position=2
    while position<len(data):
        start=position
        if data[position]!=0xff:raise ValueError('JPEG 标记无效')
        while position<len(data) and data[position]==0xff:position+=1
        if position>=len(data):raise ValueError('JPEG 标记截断')
        marker=data[position];position+=1
        if marker==0xda:
            yield marker,b'',data[start:];return
        if marker in {0xd8,0xd9,0x00} or 0xd0<=marker<=0xd7:raise ValueError('JPEG 元数据区标记无效')
        if position+2>len(data):raise ValueError('JPEG 段长度截断')
        length=struct.unpack('>H',data[position:position+2])[0];end=position+length
        if length<2 or end>len(data):raise ValueError('JPEG 段长度无效')
        yield marker,data[position+2:end],data[start:end]
        position=end
    raise ValueError('JPEG 缺少图像数据')


def _png_xmp(body):
    keyword,rest=body.split(b'\x00',1)
    if keyword!=XMP_KEY:return None
    if len(rest)<4:raise ValueError('PNG XMP iTXt 截断')
    compressed,method=rest[:2]
    if compressed not in (0,1) or method!=0:raise ValueError('PNG XMP 压缩字段不支持')
    try:_,_,text=rest[2:].split(b'\x00',2)
    except ValueError as error:raise ValueError('PNG XMP 语言字段截断') from error
    if compressed:
        decoder=zlib.decompressobj();text=decoder.decompress(text,LIMIT+1)
        if not decoder.eof:raise ValueError('PNG XMP 解压超限或截断')
    if len(text)>LIMIT:raise ValueError('XMP 元数据过大')
    return text


def _packet(data):
    packets=[]
    if data.startswith(PNG):
        for kind,body,_ in _png_chunks(data):
            if kind==b'iTXt' and body.startswith(XMP_KEY+b'\x00'):packets.append(_png_xmp(body))
    else:
        for marker,body,_ in _jpeg_segments(data):
            if marker==0xe1 and body.startswith(JPEG_XMP):packets.append(body[len(JPEG_XMP):])
            if marker==0xe1 and body.startswith(b'http://ns.adobe.com/xmp/extension/'):
                raise ValueError('扩展 XMP 需先用 ExifTool 规范化，未修改原图')
    if len(packets)>1:raise ValueError('存在多个 XMP 包，需先规范化，未修改原图')
    return packets[0] if packets else b''


def _tree(packet):
    if b'<!DOCTYPE' in packet.upper() or b'<!ENTITY' in packet.upper() or len(packet)>LIMIT:
        raise ValueError('XMP XML 不安全或过大')
    try:
        parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True,insert_pis=True))
        return ET.fromstring(packet,parser=parser)
    except ET.ParseError as error:raise ValueError('原 XMP XML 损坏，未覆盖元数据') from error


def read_xmp(path):
    packet=_packet(Path(path).read_bytes())
    if packet:_tree(packet)
    return packet


def has_tag(path):
    packet=read_xmp(path)
    if not packet:return False
    root=_tree(packet)
    return any((item.text or '').strip()==KEYWORD
               for item in root.findall(f'.//{{{DC}}}subject/{{{RDF}}}Bag/{{{RDF}}}li'))


def _merge(packet):
    root=_tree(packet) if packet else ET.Element('{'+X+'}xmpmeta')
    rdf=root if root.tag=='{'+RDF+'}RDF' else root.find('.//{'+RDF+'}RDF')
    if rdf is None:rdf=ET.SubElement(root,'{'+RDF+'}RDF')
    description=rdf.find('{'+RDF+'}Description')
    if description is None:description=ET.SubElement(rdf,'{'+RDF+'}Description')
    subject=root.find('.//{'+DC+'}subject')
    if subject is None:subject=ET.SubElement(description,'{'+DC+'}subject')
    bag=subject.find('{'+RDF+'}Bag')
    if bag is None:
        if len(subject) or (subject.text or '').strip():raise ValueError('现有 dc:subject 非 RDF Bag，未覆盖，请用 ExifTool 合并')
        bag=ET.SubElement(subject,'{'+RDF+'}Bag')
    if not any((item.text or '').strip()==KEYWORD for item in bag.findall('{'+RDF+'}li')):
        ET.SubElement(bag,'{'+RDF+'}li').text=KEYWORD
    return ET.tostring(root,encoding='utf-8')


def _png_chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)


def tag(path):
    path=Path(path);data=path.read_bytes();packet=_packet(data)
    if has_tag(path):return {'path':str(path),'tagged':True,'sha256':hashlib.sha256(data).hexdigest()}
    merged=_merge(packet)
    if data.startswith(PNG):
        chunks=list(_png_chunks(data))
        body=XMP_KEY+b'\x00\x00\x00\x00\x00'+merged;new=_png_chunk(b'iTXt',body)
        result=bytearray(PNG);inserted=False
        for kind,body,raw in chunks:
            if kind==b'iTXt' and body.startswith(XMP_KEY+b'\x00'):
                result+=new;inserted=True
            else:result+=raw
            if kind==b'IHDR' and not packet:result+=new;inserted=True
        if not inserted:raise ValueError('PNG 缺少 IHDR，未写入')
    else:
        body=JPEG_XMP+merged
        if len(body)+2>65535:raise ValueError('JPEG XMP 超过 APP1 容量，未覆盖')
        new=b'\xff\xe1'+struct.pack('>H',len(body)+2)+body;result=bytearray(b'\xff\xd8');inserted=False
        for marker,body,raw in _jpeg_segments(data):
            if marker==0xe1 and body.startswith(JPEG_XMP):result+=new;inserted=True
            else:
                if marker==0xda and not inserted:result+=new;inserted=True
                result+=raw
    fd,temporary=tempfile.mkstemp(prefix='.xmp-',suffix=path.suffix,dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(result)
        shutil.copymode(path,temporary)
        if not has_tag(temporary):raise ValueError('XMP 写后核对失败，未替换原图')
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return {'path':str(path),'tagged':True,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


tag_png=tag
tag_jpeg=tag


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['tag','check']);ap.add_argument('files',nargs='+',type=Path);args=ap.parse_args()
    try:
        for path in args.files:print(json.dumps(tag(path) if args.action=='tag' else {'path':str(path),'tagged':has_tag(path)},ensure_ascii=False))
        return 0
    except (OSError,ValueError) as error:ap.exit(2,str(error)+'\n')


if __name__=='__main__':raise SystemExit(main())
