/* Shows thumbnail chips for the files selected in the "Sell Your
 * Property" photo upload, so a seller gets confirmation of what
 * they've picked before submitting — the native file input gives no
 * feedback beyond a filename count. Vanilla JS, no dependencies.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("photos");
    var preview = document.getElementById("photoPreview");
    if (!input || !preview) return;

    input.addEventListener("change", function () {
      preview.innerHTML = "";
      Array.prototype.slice.call(input.files, 0, 10).forEach(function (file) {
        if (!file.type.startsWith("image/")) return;
        var chip = document.createElement("div");
        chip.className = "photo-preview-chip";
        var img = document.createElement("img");
        img.src = URL.createObjectURL(file);
        img.alt = file.name;
        img.onload = function () { URL.revokeObjectURL(img.src); };
        chip.appendChild(img);
        preview.appendChild(chip);
      });
      if (input.files.length > 10) {
        var note = document.createElement("p");
        note.className = "small text-muted mb-0 mt-1";
        note.textContent = "Only the first 10 photos will be submitted.";
        preview.appendChild(note);
      }
    });
  });
})();
