// Runtimes put in a new order by dragging a row's grip, with a mouse or a
// finger: the row follows the pointer among its siblings, and when let go
// the new order goes back with the form.
document.addEventListener("alpine:init", () => {
  Alpine.data("reorder", () => ({
    start(event) {
      const row = event.currentTarget.closest(".prow");
      const list = this.$root;
      const order = () => [...list.querySelectorAll(":scope > [data-index]")].map((one) => one.dataset.index).join(",");
      const before = order();
      event.preventDefault();
      row.classList.add("lifted");
      const move = (moved) => {
        const over = [...list.querySelectorAll(":scope > [data-index]")].find((one) => {
          const box = one.getBoundingClientRect();
          return one !== row && moved.clientY >= box.top && moved.clientY <= box.bottom;
        });
        if (!over) return;
        const box = over.getBoundingClientRect();
        list.insertBefore(row, moved.clientY < box.top + box.height / 2 ? over : over.nextSibling);
      };
      // the window hears the pointer: moving the row lets go of any capture
      const end = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", end);
        window.removeEventListener("pointercancel", end);
        row.classList.remove("lifted");
        const after = order();
        if (after === before) return;
        const form = list.closest("form");
        form.elements.order.value = after;
        form.requestSubmit(this.$refs.moved);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", end);
      window.addEventListener("pointercancel", end);
    },
  }));
});
