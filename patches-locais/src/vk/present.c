// Vulkan presentation (see present.h). The functions are loaded through SDL's vkGetInstanceProcAddr, so the exe does
// not link vulkan-1 and starts (without a picture) on a machine with no Vulkan driver.
// The present thread waits for a frame from the game, acquires a swapchain image, fits the newest game frame into it
// (the replayer's output image) and presents it. The game never waits on the display here; its pacing comes from the vsync events (front_win.c).
// Mailbox when the surface offers it (the newest frame wins, no tearing), FIFO otherwise.
#include "present.h"
#include "vk.h"

#include <SDL3/SDL.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define LOG(...) (fprintf(stderr, "[totk-vk] " __VA_ARGS__), fputc('\n', stderr))
#define FRAMES 2          // frames in flight
#define MAX_IMAGES 8

static SDL_Window *g_window;
static VkSwapchainKHR swapchain;
static VkFormat format;
static VkExtent2D extent;
static VkImage images[MAX_IMAGES];
static uint32_t nimages;
static VkSemaphore image_ready[FRAMES], render_done[MAX_IMAGES];
static VkFence in_flight[FRAMES];
static VkCommandPool pool;
static VkCommandBuffer cmd[FRAMES];
static VkPresentModeKHR mode;

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static uint64_t frames_in;           // under mu
static _Atomic uint64_t presented;
static _Atomic int resized;

// Command buffers and sync objects of the present thread (the device is vk.c's).
static int create_present_objects(void) {
    VkCommandPoolCreateInfo pi = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
                                  .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, .queueFamilyIndex = vkc.family};
    if (!vk_check(vk.vkCreateCommandPool(vkc.device, &pi, NULL, &pool), "vkCreateCommandPool")) return 0;
    VkCommandBufferAllocateInfo ai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = pool,
                                      .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = FRAMES};
    if (!vk_check(vk.vkAllocateCommandBuffers(vkc.device, &ai, cmd), "vkAllocateCommandBuffers")) return 0;
    VkSemaphoreCreateInfo si = {VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
    VkFenceCreateInfo fi = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, .flags = VK_FENCE_CREATE_SIGNALED_BIT};
    for (int i = 0; i < FRAMES; i++)
        if (!vk_check(vk.vkCreateSemaphore(vkc.device, &si, NULL, &image_ready[i]), "vkCreateSemaphore") ||
            !vk_check(vk.vkCreateFence(vkc.device, &fi, NULL, &in_flight[i]), "vkCreateFence")) return 0;
    return 1;
}

static void destroy_swapchain(void) {
    for (uint32_t i = 0; i < nimages; i++) vk.vkDestroySemaphore(vkc.device, render_done[i], NULL);
    if (swapchain) vk.vkDestroySwapchainKHR(vkc.device, swapchain, NULL);
    swapchain = VK_NULL_HANDLE;
    nimages = 0;
}

// 0: the window has no area now (minimised) — try again later
static int create_swapchain(void) {
    VkSurfaceCapabilitiesKHR caps;
    if (!vk_check(vk.vkGetPhysicalDeviceSurfaceCapabilitiesKHR(vkc.gpu, vkc.surface, &caps), "surface capabilities")) return 0;
    extent = caps.currentExtent;
    if (extent.width == UINT32_MAX) {
        int w, h;
        SDL_GetWindowSizeInPixels(g_window, &w, &h);
        extent = (VkExtent2D){(uint32_t)w, (uint32_t)h};
    }
    if (!extent.width || !extent.height) return 0;
    uint32_t nf = 0;
    vk.vkGetPhysicalDeviceSurfaceFormatsKHR(vkc.gpu, vkc.surface, &nf, NULL);
    VkSurfaceFormatKHR *fm = calloc(nf ? nf : 1, sizeof *fm);
    vk.vkGetPhysicalDeviceSurfaceFormatsKHR(vkc.gpu, vkc.surface, &nf, fm);
    VkSurfaceFormatKHR pick = fm[0];
    for (uint32_t i = 0; i < nf; i++)
        if ((fm[i].format == VK_FORMAT_B8G8R8A8_UNORM || fm[i].format == VK_FORMAT_R8G8B8A8_UNORM) &&
            fm[i].colorSpace == VK_COLOR_SPACE_SRGB_NONLINEAR_KHR) { pick = fm[i]; break; }
    free(fm);
    format = pick.format;
    uint32_t nm = 0;
    vk.vkGetPhysicalDeviceSurfacePresentModesKHR(vkc.gpu, vkc.surface, &nm, NULL);
    VkPresentModeKHR modes[16];
    if (nm > 16) nm = 16;
    vk.vkGetPhysicalDeviceSurfacePresentModesKHR(vkc.gpu, vkc.surface, &nm, modes);
    mode = VK_PRESENT_MODE_FIFO_KHR;
    for (uint32_t i = 0; i < nm; i++) if (modes[i] == VK_PRESENT_MODE_MAILBOX_KHR) mode = VK_PRESENT_MODE_MAILBOX_KHR;
    uint32_t count = caps.minImageCount + 1;
    if (caps.maxImageCount && count > caps.maxImageCount) count = caps.maxImageCount;
    VkSwapchainCreateInfoKHR ci = {VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR, .surface = vkc.surface,
        .minImageCount = count, .imageFormat = format, .imageColorSpace = pick.colorSpace, .imageExtent = extent,
        .imageArrayLayers = 1, .imageUsage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
        .imageSharingMode = VK_SHARING_MODE_EXCLUSIVE, .preTransform = caps.currentTransform,
        .compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR, .presentMode = mode, .clipped = VK_TRUE};
    if (!vk_check(vk.vkCreateSwapchainKHR(vkc.device, &ci, NULL, &swapchain), "vkCreateSwapchainKHR")) return 0;
    nimages = MAX_IMAGES;
    vk.vkGetSwapchainImagesKHR(vkc.device, swapchain, &nimages, images);
    VkSemaphoreCreateInfo si = {VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
    for (uint32_t i = 0; i < nimages; i++) vk.vkCreateSemaphore(vkc.device, &si, NULL, &render_done[i]);
    LOG("swapchain %ux%u, %u images, %s", extent.width, extent.height, nimages,
        mode == VK_PRESENT_MODE_MAILBOX_KHR ? "mailbox" : "fifo");
    return 1;
}

static void barrier(VkCommandBuffer cb, VkImage img, VkImageLayout from, VkImageLayout to, VkAccessFlags src,
                    VkAccessFlags dst, VkPipelineStageFlags s0, VkPipelineStageFlags s1) {
    VkImageMemoryBarrier b = {VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, .srcAccessMask = src, .dstAccessMask = dst,
        .oldLayout = from, .newLayout = to, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = img,
        .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
    vk.vkCmdPipelineBarrier(cb, s0, s1, 0, 0, NULL, 0, NULL, 1, &b);
}

// Until the first game frame (and for the null device): a colour that cycles with the frame number.
static VkClearColorValue frame_colour(uint64_t n) {
    float t = (float)(n % 240) / 240.0f * 6.2831853f;
    return (VkClearColorValue){{0.15f + 0.1f * sinf(t), 0.15f + 0.1f * sinf(t + 2.094f), 0.2f + 0.1f * sinf(t + 4.188f), 1}};
}

// ---- output images: written by the replay thread, shown by the present thread
#define OUTS 3
typedef struct {
    VkImage img; VkDeviceMemory mem; uint32_t w, h;
    int state;                   // 0 free, 1 written (pending), 2 being shown
    uint64_t seq;                // when it was written (newest pending wins)
    VkSemaphore timeline; uint64_t value; int crop[4];
} out_t;
static out_t g_out[OUTS];
static uint64_t g_out_seq;       // under mu

static uint32_t memory_type(uint32_t bits, VkMemoryPropertyFlags want) {
    for (uint32_t i = 0; i < vkc.memory.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (vkc.memory.memoryTypes[i].propertyFlags & want) == want) return i;
    return UINT32_MAX;
}

int vkp_active(void) { return swapchain != VK_NULL_HANDLE || g_window != NULL; }

// ---- text overlay (the trainer's menu, src/host/trainer.c): UTF-8 lines drawn with GDI into a host buffer, copied into
// an image and blitted over the picture at the top left, scaled with the window (1:1 at 1080p). A line starting with
// '#' is dim (headers, help), with '>' highlighted (the cursor). vkp_overlay_set("") hides it.
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#endif
#define OVL_W 760
#define OVL_H 724
static pthread_mutex_t ovl_mu = PTHREAD_MUTEX_INITIALIZER;
static char ovl_text[8192];
static int ovl_version = 1, ovl_rendered;
static uint32_t *ovl_pixels;                 // BGRA OVL_W x OVL_H, rendered on the present thread when the version changes
static VkBuffer ovl_buf[FRAMES];
static VkDeviceMemory ovl_bmem[FRAMES], ovl_imem;
static void *ovl_map[FRAMES];
static VkImage ovl_img;
static int ovl_ready = -1;

void vkp_overlay_set(const char *text) {
    pthread_mutex_lock(&ovl_mu);
    snprintf(ovl_text, sizeof ovl_text, "%s", text ? text : "");
    ovl_version++;
    pthread_mutex_unlock(&ovl_mu);
}
static int ovl_create(void) {
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = (VkDeviceSize)OVL_W * OVL_H * 4,
                             .usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
    for (int i = 0; i < FRAMES; i++) {
        if (!vk_check(vk.vkCreateBuffer(vkc.device, &bi, NULL, &ovl_buf[i]), "overlay buffer")) return 0;
        VkMemoryRequirements req;
        vk.vkGetBufferMemoryRequirements(vkc.device, ovl_buf[i], &req);
        VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = req.size,
            .memoryTypeIndex = memory_type(req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)};
        if (ai.memoryTypeIndex == UINT32_MAX || !vk_check(vk.vkAllocateMemory(vkc.device, &ai, NULL, &ovl_bmem[i]), "overlay memory")) return 0;
        vk.vkBindBufferMemory(vkc.device, ovl_buf[i], ovl_bmem[i], 0);
        if (!vk_check(vk.vkMapMemory(vkc.device, ovl_bmem[i], 0, VK_WHOLE_SIZE, 0, &ovl_map[i]), "overlay map")) return 0;
    }
    VkImageCreateInfo ci = {VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_B8G8R8A8_UNORM,
        .extent = {OVL_W, OVL_H, 1}, .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
    if (!vk_check(vk.vkCreateImage(vkc.device, &ci, NULL, &ovl_img), "overlay image")) return 0;
    VkMemoryRequirements req;
    vk.vkGetImageMemoryRequirements(vkc.device, ovl_img, &req);
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = req.size,
                               .memoryTypeIndex = memory_type(req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)};
    if (!vk_check(vk.vkAllocateMemory(vkc.device, &ai, NULL, &ovl_imem), "overlay image memory")) return 0;
    vk.vkBindImageMemory(vkc.device, ovl_img, ovl_imem, 0);
    ovl_pixels = calloc((size_t)OVL_W * OVL_H, 4);
    LOG("text overlay ready (%dx%d)", OVL_W, OVL_H);
    return ovl_pixels != NULL;
}
static void ovl_render(const char *text) {
#ifdef _WIN32
    HDC dc = CreateCompatibleDC(NULL);
    BITMAPINFO bmi = {0};
    bmi.bmiHeader.biSize = sizeof bmi.bmiHeader; bmi.bmiHeader.biWidth = OVL_W; bmi.bmiHeader.biHeight = -OVL_H;
    bmi.bmiHeader.biPlanes = 1; bmi.bmiHeader.biBitCount = 32; bmi.bmiHeader.biCompression = BI_RGB;
    void *bits = NULL;
    HBITMAP bmp = CreateDIBSection(dc, &bmi, DIB_RGB_COLORS, &bits, NULL, 0);
    if (!bmp || !bits) { if (bmp) DeleteObject(bmp); DeleteDC(dc); return; }
    HGDIOBJ oldbmp = SelectObject(dc, bmp);
    HFONT font = CreateFontW(-19, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                             ANTIALIASED_QUALITY, FIXED_PITCH | FF_MODERN, L"Consolas");
    HGDIOBJ oldfont = SelectObject(dc, font);
    RECT r = {0, 0, OVL_W, OVL_H};
    HBRUSH bg = CreateSolidBrush(RGB(14, 16, 26)); FillRect(dc, &r, bg); DeleteObject(bg);
    RECT stripe = {0, 0, OVL_W, 4};
    HBRUSH hb = CreateSolidBrush(RGB(230, 180, 40)); FillRect(dc, &stripe, hb); DeleteObject(hb);
    SetBkMode(dc, TRANSPARENT);
    int y = 12;
    for (const char *p = text; *p && y < OVL_H - 22;) {
        const char *nl = strchr(p, '\n');
        size_t n = nl ? (size_t)(nl - p) : strlen(p);
        wchar_t w[512];
        int wn = n ? MultiByteToWideChar(CP_UTF8, 0, p, (int)n, w, 511) : 0;
        const wchar_t *ws = w;
        COLORREF col = RGB(235, 235, 240);
        if (p[0] == '>') col = RGB(255, 220, 90);
        else if (p[0] == '#') { col = RGB(150, 160, 190); ws = w + 1; wn--; }
        SetTextColor(dc, col);
        if (wn > 0) TextOutW(dc, 16, y, ws, wn);
        y += 22;
        p = nl ? nl + 1 : p + n;
    }
    GdiFlush();
    memcpy(ovl_pixels, bits, (size_t)OVL_W * OVL_H * 4);
    SelectObject(dc, oldfont); SelectObject(dc, oldbmp);
    DeleteObject(font); DeleteObject(bmp); DeleteDC(dc);
#else
    (void)text;
#endif
}
// Inside the present command buffer, with dst (the swapchain image) in TRANSFER_DST layout.
static void ovl_draw(VkCommandBuffer cb, unsigned slot, VkImage dst) {
    static char text[sizeof ovl_text];
    int version;
    pthread_mutex_lock(&ovl_mu);
    version = ovl_version;
    memcpy(text, ovl_text, sizeof text);
    pthread_mutex_unlock(&ovl_mu);
    if (!text[0]) return;
    if (ovl_ready < 0) ovl_ready = ovl_create();
    if (!ovl_ready) return;
    if (ovl_rendered != version) { ovl_render(text); ovl_rendered = version; }
    memcpy(ovl_map[slot], ovl_pixels, (size_t)OVL_W * OVL_H * 4);   // the slot's last command buffer is done (in_flight fence)
    barrier(cb, ovl_img, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT,
            VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
    VkBufferImageCopy c = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, .imageExtent = {OVL_W, OVL_H, 1}};
    vk.vkCmdCopyBufferToImage(cb, ovl_buf[slot], ovl_img, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &c);
    barrier(cb, ovl_img, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_TRANSFER_WRITE_BIT,
            VK_ACCESS_TRANSFER_READ_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
    double k = extent.height / 1080.0;
    if (k < 0.6) k = 0.6; else if (k > 3) k = 3;
    int dx = (int)(24 * k), dy = (int)(24 * k), dw = (int)(OVL_W * k), dh = (int)(OVL_H * k), sw = OVL_W, sh = OVL_H;
    if (dx + dw > (int)extent.width) { dw = (int)extent.width - dx; sw = (int)(dw / k); }
    if (dy + dh > (int)extent.height) { dh = (int)extent.height - dy; sh = (int)(dh / k); }
    if (dw <= 0 || dh <= 0 || sw <= 0 || sh <= 0) return;
    VkImageBlit b = {{VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {{0, 0, 0}, {sw, sh, 1}},
                     {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {{dx, dy, 0}, {dx + dw, dy + dh, 1}}};
    vk.vkCmdBlitImage(cb, ovl_img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &b, VK_FILTER_LINEAR);
}

int vkp_out_acquire(uint32_t w, uint32_t h, VkImage *image) {
    pthread_mutex_lock(&mu);
    int pick = -1;
    for (int i = 0; i < OUTS && pick < 0; i++) if (g_out[i].state == 0) pick = i;
    if (pick < 0)   // none free: overwrite the oldest pending one (it was never shown; newer frames win)
        for (int i = 0; i < OUTS; i++) if (g_out[i].state == 1 && (pick < 0 || g_out[i].seq < g_out[pick].seq)) pick = i;
    if (pick >= 0) g_out[pick].state = 3;   // being written
    pthread_mutex_unlock(&mu);
    if (pick < 0) return -1;
    out_t *o = &g_out[pick];
    if (o->img && (o->w != w || o->h != h)) {   // free: no present work uses it
        vk.vkDestroyImage(vkc.device, o->img, NULL);
        vk.vkFreeMemory(vkc.device, o->mem, NULL);
        o->img = VK_NULL_HANDLE;
    }
    if (!o->img) {
        VkImageCreateInfo ci = {VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_B8G8R8A8_UNORM,
            .extent = {w, h, 1}, .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
            .usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
        VkMemoryRequirements req;
        if (!vk_check(vk.vkCreateImage(vkc.device, &ci, NULL, &o->img), "output image")) goto fail;
        vk.vkGetImageMemoryRequirements(vkc.device, o->img, &req);
        VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = req.size,
                                   .memoryTypeIndex = memory_type(req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)};
        if (!vk_check(vk.vkAllocateMemory(vkc.device, &ai, NULL, &o->mem), "output memory")) {
            vk.vkDestroyImage(vkc.device, o->img, NULL);
            o->img = VK_NULL_HANDLE;
            goto fail;
        }
        vk.vkBindImageMemory(vkc.device, o->img, o->mem, 0);
        o->w = w; o->h = h;
    }
    *image = o->img;
    return pick;
fail:
    pthread_mutex_lock(&mu);
    o->state = 0;
    pthread_mutex_unlock(&mu);
    return -1;
}

void vkp_out_ready(int slot, VkSemaphore timeline, uint64_t value, const int crop[4]) {
    pthread_mutex_lock(&mu);
    out_t *o = &g_out[slot];
    o->timeline = timeline; o->value = value; o->seq = ++g_out_seq; o->state = 1;
    if (crop) memcpy(o->crop, crop, sizeof o->crop); else memset(o->crop, 0, sizeof o->crop);
    for (int i = 0; i < OUTS; i++)   // older pending frames are never shown
        if (i != slot && g_out[i].state == 1 && g_out[i].seq < o->seq) g_out[i].state = 0;
    frames_in++;
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
}

// One window frame: the newest game frame (out, may be NULL: the cycling colour) fitted with its aspect ratio.
static void ovl_draw(VkCommandBuffer cb, unsigned slot, VkImage dst);
static int present_one(uint64_t n, unsigned slot, out_t *out) {
    if (atomic_exchange(&resized, 0) || !swapchain) {
        vk.vkWaitForFences(vkc.device, FRAMES, in_flight, VK_TRUE, UINT64_MAX);   // the present thread's own work only
        destroy_swapchain();
        if (!create_swapchain()) return 0;
    }
    vk.vkWaitForFences(vkc.device, 1, &in_flight[slot], VK_TRUE, UINT64_MAX);
    uint32_t idx;
    VkResult r = vk.vkAcquireNextImageKHR(vkc.device, swapchain, UINT64_MAX, image_ready[slot], VK_NULL_HANDLE, &idx);
    if (r == VK_ERROR_OUT_OF_DATE_KHR) { atomic_store(&resized, 1); return 0; }
    if (r != VK_SUCCESS && r != VK_SUBOPTIMAL_KHR) { vk_check(r, "vkAcquireNextImageKHR"); return 0; }
    vk.vkResetFences(vkc.device, 1, &in_flight[slot]);
    VkCommandBuffer cb = cmd[slot];
    vk.vkResetCommandBuffer(cb, 0);
    VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                   .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    vk.vkBeginCommandBuffer(cb, &bi);
    barrier(cb, images[idx], VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0,
            VK_ACCESS_TRANSFER_WRITE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
    VkClearColorValue c = out ? (VkClearColorValue){{0, 0, 0, 1}} : frame_colour(n);   // bars around the game picture
    VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vk.vkCmdClearColorImage(cb, images[idx], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &c, 1, &range);
    if (out) {
        // source rectangle: the game's crop (NVN WindowCrop; 0 size = the whole texture)
        int sx = out->crop[0], sy = out->crop[1], sw = out->crop[2], sh = out->crop[3];
        if (sw <= 0 || sh <= 0 || sx + sw > (int)out->w || sy + sh > (int)out->h) { sx = sy = 0; sw = (int)out->w; sh = (int)out->h; }
        double scale = (double)extent.width / sw < (double)extent.height / sh ? (double)extent.width / sw : (double)extent.height / sh;
        int dw = (int)(sw * scale + 0.5), dh = (int)(sh * scale + 0.5), dx = ((int)extent.width - dw) / 2, dy = ((int)extent.height - dh) / 2;
        barrier(cb, images[idx], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_TRANSFER_WRITE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
        VkImageBlit b = {{VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {{sx, sy, 0}, {sx + sw, sy + sh, 1}},
                         {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {{dx, dy, 0}, {dx + dw, dy + dh, 1}}};
        vk.vkCmdBlitImage(cb, out->img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, images[idx], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                          1, &b, VK_FILTER_LINEAR);
    }
    ovl_draw(cb, slot, images[idx]);   // the trainer's menu, when open
    barrier(cb, images[idx], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
            VK_ACCESS_TRANSFER_WRITE_BIT, 0, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT);
    vk.vkEndCommandBuffer(cb);
    // wait for the swapchain image and, with a game frame, for the replayer's copy into the output image
    VkSemaphoreSubmitInfo waits[2] = {
        {VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO, .semaphore = image_ready[slot], .stageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT},
        {VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO, .semaphore = out ? out->timeline : VK_NULL_HANDLE, .value = out ? out->value : 0,
         .stageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT}};
    VkSemaphoreSubmitInfo sig = {VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO, .semaphore = render_done[idx],
                                 .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT};
    VkCommandBufferSubmitInfo cbi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO, .commandBuffer = cb};
    VkSubmitInfo2 si = {VK_STRUCTURE_TYPE_SUBMIT_INFO_2, .waitSemaphoreInfoCount = out ? 2u : 1u, .pWaitSemaphoreInfos = waits,
                        .commandBufferInfoCount = 1, .pCommandBufferInfos = &cbi, .signalSemaphoreInfoCount = 1,
                        .pSignalSemaphoreInfos = &sig};
    VkPresentInfoKHR pi = {VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, .waitSemaphoreCount = 1,
                           .pWaitSemaphores = &render_done[idx], .swapchainCount = 1, .pSwapchains = &swapchain,
                           .pImageIndices = &idx};
    vk_queue_lock();   // the replayer submits on the same queue
    VkResult s = vk.vkQueueSubmit2(vkc.queue, 1, &si, in_flight[slot]);
    r = s == VK_SUCCESS ? vk.vkQueuePresentKHR(vkc.queue, &pi) : s;
    vk_queue_unlock();
    if (!vk_check(s, "vkQueueSubmit2")) return 0;
    if (r == VK_ERROR_OUT_OF_DATE_KHR || r == VK_SUBOPTIMAL_KHR) atomic_store(&resized, 1);
    else if (r != VK_SUCCESS) { vk_check(r, "vkQueuePresentKHR"); return 0; }
    if (out) vk.vkWaitForFences(vkc.device, 1, &in_flight[slot], VK_TRUE, UINT64_MAX);   // the output image is free again after this
    atomic_fetch_add(&presented, 1);
    return 1;
}

static void *present_thread(void *unused) {
    (void)unused;
    uint64_t shown = 0;
    unsigned slot = 0;
    for (;;) {
        pthread_mutex_lock(&mu);
        while (frames_in == shown) pthread_cond_wait(&cv, &mu);
        shown = frames_in;   // frames that came while the last one was shown are skipped: only the newest counts
        int pick = -1;
        for (int i = 0; i < OUTS; i++) if (g_out[i].state == 1 && (pick < 0 || g_out[i].seq > g_out[pick].seq)) pick = i;
        if (pick >= 0) g_out[pick].state = 2;
        pthread_mutex_unlock(&mu);
        if (present_one(shown, slot, pick >= 0 ? &g_out[pick] : NULL)) slot = (slot + 1) % FRAMES;
        if (pick >= 0) {
            pthread_mutex_lock(&mu);
            g_out[pick].state = 0;
            pthread_mutex_unlock(&mu);
        }
    }
    return NULL;
}

int vkp_init(SDL_Window *window) {
    g_window = window;
    if (!vk_init(window) || !vkc.surface || !create_present_objects()) return 0;
    if (!create_swapchain()) LOG("window has no area yet; swapchain deferred");
    pthread_t t;
    if (pthread_create(&t, NULL, present_thread, NULL)) { LOG("present thread"); return 0; }
    return 1;
}

void vkp_frame(void) {
    pthread_mutex_lock(&mu);
    frames_in++;
    pthread_cond_signal(&cv);
    pthread_mutex_unlock(&mu);
}

void vkp_resized(void) { atomic_store(&resized, 1); }
uint64_t vkp_presented(void) { return atomic_load(&presented); }
uint64_t vkp_frames(void) {
    pthread_mutex_lock(&mu);
    uint64_t n = frames_in;
    pthread_mutex_unlock(&mu);
    return n;
}
