/* Property detail page gallery: syncs the thumbnail strip with the
 * Bootstrap carousel (Bootstrap only auto-manages .active on indicators
 * that live inside the carousel itself — this strip sits outside it so
 * it can wrap independently), plus a lightweight lightbox. Vanilla JS,
 * no framework, matches the rest of the public site's zero-dependency
 * approach.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var carouselEl = document.getElementById("propertyGallery");
    var thumbs = document.querySelectorAll(".gallery-thumb");
    if (carouselEl && thumbs.length) {
      carouselEl.addEventListener("slid.bs.carousel", function (event) {
        thumbs.forEach(function (thumb, index) {
          thumb.classList.toggle("active", index === event.to);
        });
      });
    }

    var triggers = Array.prototype.slice.call(document.querySelectorAll(".gallery-zoom-trigger"));
    if (!triggers.length) return;

    var lightbox = document.createElement("div");
    lightbox.className = "gallery-lightbox";
    lightbox.hidden = true;
    lightbox.setAttribute("role", "dialog");
    lightbox.setAttribute("aria-modal", "true");
    lightbox.setAttribute("aria-label", "Image viewer");
    lightbox.innerHTML =
      '<button type="button" class="gallery-lightbox-close" aria-label="Close">' +
      '<i class="bi bi-x-lg"></i></button>' +
      (triggers.length > 1
        ? '<button type="button" class="gallery-lightbox-prev" aria-label="Previous image">' +
          '<i class="bi bi-chevron-left"></i></button>' +
          '<button type="button" class="gallery-lightbox-next" aria-label="Next image">' +
          '<i class="bi bi-chevron-right"></i></button>'
        : "") +
      '<img alt="">' +
      '<p class="gallery-lightbox-caption"></p>';
    document.body.appendChild(lightbox);

    var imgEl = lightbox.querySelector("img");
    var captionEl = lightbox.querySelector(".gallery-lightbox-caption");
    var closeBtn = lightbox.querySelector(".gallery-lightbox-close");
    var prevBtn = lightbox.querySelector(".gallery-lightbox-prev");
    var nextBtn = lightbox.querySelector(".gallery-lightbox-next");
    var currentIndex = 0;
    var lastFocused = null;

    function show(index) {
      currentIndex = (index + triggers.length) % triggers.length;
      var trigger = triggers[currentIndex];
      imgEl.src = trigger.dataset.full;
      imgEl.alt = trigger.dataset.caption || "";
      captionEl.textContent = trigger.dataset.caption || "";
    }

    function open(index, opener) {
      lastFocused = opener || document.activeElement;
      show(index);
      lightbox.hidden = false;
      document.body.style.overflow = "hidden";
      closeBtn.focus();
    }

    function close() {
      lightbox.hidden = true;
      document.body.style.overflow = "";
      if (lastFocused) lastFocused.focus();
    }

    triggers.forEach(function (trigger, index) {
      trigger.addEventListener("click", function () {
        open(index, trigger);
      });
    });

    closeBtn.addEventListener("click", close);
    if (prevBtn) prevBtn.addEventListener("click", function () { show(currentIndex - 1); });
    if (nextBtn) nextBtn.addEventListener("click", function () { show(currentIndex + 1); });

    lightbox.addEventListener("click", function (event) {
      if (event.target === lightbox) close();
    });

    document.addEventListener("keydown", function (event) {
      if (lightbox.hidden) return;
      if (event.key === "Escape") close();
      else if (event.key === "ArrowLeft" && prevBtn) show(currentIndex - 1);
      else if (event.key === "ArrowRight" && nextBtn) show(currentIndex + 1);
    });
  });
})();
