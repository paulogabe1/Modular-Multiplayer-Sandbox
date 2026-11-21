// Base for every granted ability. One C++ subclass per ability, optionally a Blueprint subclass for VFX.
#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "Abilities/AbilityTypes.h"
#include "AbilityComponentBase.generated.h"

class UAbilityLoadoutManager;
class APawn;

/**
 * Lives on the pawn and is derived locally on every machine from the replicated token list.
 * It is deliberately NOT replicated: all shared state travels on UAbilityLoadoutManager.
 */
UCLASS(Blueprintable, BlueprintType, ClassGroup = Abilities)
class UAbilityComponentBase : public UActorComponent
{
	GENERATED_BODY()

public:
	UAbilityComponentBase();

	UPROPERTY(BlueprintReadOnly, Category = "Ability")
	FName Token;

	UPROPERTY(BlueprintReadOnly, Category = "Ability")
	EAbilityCategory Category = EAbilityCategory::Offense;

	UPROPERTY(BlueprintReadOnly, Category = "Ability")
	TMap<FName, float> Params;

	/** Called right after creation, on server and clients alike. */
	void ConfigureAbility(FName InToken, const FAbilityTokenDefinition& Definition, const TMap<FName, float>& InParams, UAbilityLoadoutManager* InManager, bool bInHasAuthority);

	UFUNCTION(BlueprintPure, Category = "Ability")
	float GetParam(FName ParamName, float Fallback = 0.f) const;

	UFUNCTION(BlueprintPure, Category = "Ability")
	APawn* GetAvatarPawn() const;

	UFUNCTION(BlueprintPure, Category = "Ability")
	float GetCooldownRemaining() const;

	/**
	 * Server only. Returns how long the effect stays active (0 = instant).
	 *
	 * Named BeginEffect/EndEffect rather than Activate/Deactivate because UActorComponent already
	 * declares Activate(bool) and Deactivate(). The old Deactivate() matched that base signature
	 * exactly, so it silently overrode the component-lifecycle function: engine-driven deactivation
	 * called the ability's version instead. These names collide with nothing.
	 */
	virtual float BeginEffect();

	/** Server only, called when the active duration expires. */
	virtual void EndEffect();

	/** Server only gate. */
	virtual bool CanActivate() const;

	/** Runs on the server and on every client; drives the visual half of the ability. */
	virtual void HandleCosmetic(bool bStarting);

	/** Blueprint hook for VFX and sounds. */
	UFUNCTION(BlueprintImplementableEvent, Category = "Ability", meta = (DisplayName = "On Ability Cosmetic"))
	void BP_OnCosmetic(bool bStarting);

	UFUNCTION(BlueprintPure, Category = "Ability")
	bool HasAuthority() const { return bHasAuthority; }

protected:
	UPROPERTY()
	TObjectPtr<UAbilityLoadoutManager> LoadoutManager;

	UPROPERTY()
	bool bHasAuthority = false;
};
