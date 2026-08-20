/* The sample shop's one interaction, in a file rather than inline so the demo
   page obeys the same content security policy as everything else. */
document.getElementById("buy").addEventListener("click", function () {
  if (window.beacon) window.beacon("add-to-basket");
  this.textContent = "Added";
});
