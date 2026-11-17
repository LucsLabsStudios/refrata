// What rtx.cpp needs from vout.cpp's router (UI thread, or the render thread during sync).
#pragma once
#include <QtMultimedia/QVideoFrame>

class RtxVideo;

// the picture on screen and its sequence number (changes with every new picture)
QVideoFrame rv_last(void *router, quint64 *seq);
// items told of every new picture (RtxVideo::pictureArrived)
void rv_add_viewer(void *router, RtxVideo *item);
void rv_remove_viewer(void *router, RtxVideo *item);
